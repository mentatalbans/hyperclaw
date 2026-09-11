#!/usr/bin/env python3
"""Explicit, opt-in documentation answer measurement; no downloads or retries."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import os
import shutil
import subprocess
import tempfile
import traceback

import httpx
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # Test harness only; installed hyperclaw remains required.
from scripts.measure_runtime import source_identity, environment_identity, git_output
from tests.support.process import Process, sse_events
from tests.live.test_recovery_docker import cleanup_owned, owned_container_ids


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(directory):
    return {str(p.relative_to(directory)): digest(p) for p in sorted(directory.rglob('*')) if p.is_file()}


def tool_attempts(value):
    """Union raw starts, checkpoint calls and invocations, with round-aware origins."""
    calls = {}
    def add(call, origin, round_index=None):
        if not isinstance(call, dict) or not call.get('name'):
            return
        call_id = call.get('id')
        if not isinstance(call_id, str):
            call_id = json.dumps(call, sort_keys=True)
        key = (round_index, call_id)
        if round_index is None:
            matches = [k for k in calls if k[1] == call_id]
            if matches:
                key = matches[-1]
        entry = calls.setdefault(key, {'id': call.get('id'), 'name': call['name'],
            'request_index': key[0], 'arguments': call.get('arguments', call.get('input', {})), 'origins': []})
        if origin not in entry['origins']:
            entry['origins'].append(origin)
        if call.get('arguments'):
            entry['arguments'] = call['arguments']
    streams = defaultdict(str)
    for row in value.get('wire', []):
        if row.get('kind') == 'response_chunk':
            streams[row['request_index']] += row['text']
    for index, stream in streams.items():
        for frame in re.split(r'\r?\n\r?\n', stream):
            data = '\n'.join(line[5:].lstrip() for line in frame.splitlines() if line.startswith('data:'))
            try:
                event = json.loads(data)
            except (ValueError, TypeError):
                continue  # Retained raw malformed bytes cannot prove an executed call.
            if isinstance(event, dict) and event.get('type') == 'content_block_start':
                block = event.get('content_block', {})
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    add(block, f'wire.request.{index}', index)
    cp = value.get('checkpoint') or {}
    round_index = 0
    for message in cp.get('messages', [])[cp.get('history_length', 0):]:
        if message.get('role') == 'assistant':
            round_index += 1
        for block in message.get('content', []) if isinstance(message.get('content'), list) else []:
            if block.get('type') == 'tool_use':
                add(block, 'checkpoint.messages', round_index or None)
    for call in cp.get('pending_calls', []):
        add(call, 'checkpoint.pending_calls', cp.get('round_count') or None)
    for inv in value.get('invocations', []):
        add(inv.get('call', {}), 'invocations')
    return list(calls.values())


def wire_parse_errors(value):
    streams = defaultdict(str)
    for row in value.get('wire', []):
        if row.get('kind') == 'response_chunk':
            streams[row['request_index']] += row['text']
    errors = 0
    for stream in streams.values():
        for frame in re.split(r'\r?\n\r?\n', stream):
            if not frame.strip():
                continue
            data = '\n'.join(line[5:].lstrip() for line in frame.splitlines() if line.startswith('data:'))
            if not data and all(line.startswith(':') for line in frame.splitlines()):
                continue
            try:
                event = json.loads(data)
                if not isinstance(event, dict):
                    raise ValueError('non-object event')
                block = event.get('content_block', {})
                if event.get('type') == 'content_block_start' and (
                    not isinstance(block, dict) or (block.get('type') == 'tool_use' and (
                        not isinstance(block.get('name'), str) or not isinstance(block.get('id'), str)))):
                    raise ValueError('malformed tool start')
            except (ValueError, TypeError):
                errors += 1
    return errors


def model_observation(wire, selected):
    requested = sorted({row['body']['model'] for row in wire if row['kind'] == 'request'})
    streams = defaultdict(str)
    for row in wire:
        if row['kind'] == 'response_chunk':
            streams[row['request_index']] += row['text']
    models = set()
    for stream in streams.values():
        for frame in re.split(r'\r?\n\r?\n', stream):
            data = '\n'.join(line[5:].lstrip() for line in frame.splitlines() if line.startswith('data:'))
            try:
                event = json.loads(data)
                if event.get('type') == 'message_start':
                    model = event.get('message', {}).get('model')
                    if isinstance(model, str) and model:
                        models.add(model)
            except (ValueError, TypeError, AttributeError):
                continue
    return {'requested_models': requested, 'response_models': sorted(models),
            'response_identity': 'observed' if models else 'unavailable',
            'response_matches_selection': models == {selected} if models else None}


def source_matches(source, docs):
    path = docs / source.get('path', '')
    if docs.resolve() not in path.resolve().parents or not path.is_file():
        return False
    if source.get('sha256') != digest(path):
        return False
    lines = path.read_text().splitlines()
    start = source.get('start_line', source.get('line'))
    end = source.get('end_line', start)
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
        return False
    # The peer may truncate search lines/read text. Verify the exact returned prefix.
    expected = '\n'.join(lines[start-1:end])
    size = 8192 if 'start_line' in source else 512
    expected = expected.encode()[:size].decode(errors='ignore')
    return source.get('text') == expected


def score_trial(case, value, docs):
    answer = (value.get('run') or {}).get('output') or value.get('partial_output', '')
    retrieved, source_errors, verified_sources = set(), [], []
    for receipt in value.get('receipts', []):
        sources = receipt.get('evidence', {}).get('sources', [])
        try:
            delivered = json.loads(receipt.get('output', ''))
            delivered_sources = delivered.get('hits', [delivered])
            output_matches = delivered_sources == sources
        except (ValueError, TypeError, AttributeError):
            output_matches = False
        if not output_matches:
            source_errors.append({'kind': 'receipt_output_mismatch', 'invocation_id': receipt.get('invocation_id')})
        for source in sources:
            if output_matches and receipt.get('status') == 'succeeded' and source_matches(source, docs):
                retrieved.add(source['path'])
                verified_sources.append(source)
            else:
                source_errors.append({'path': source.get('path'), 'invocation_id': receipt.get('invocation_id')})
    facts = case['expected_facts']
    missing = [f['id'] for f in facts if not any(re.search(p, answer, re.I) for p in f['patterns'])]
    unsupported_facts = [f['id'] for f in facts if f['id'] in missing or not any(
        source['path'] == interval['path']
        and source.get('start_line', source.get('line')) <= interval['start_line']
        and source.get('end_line', source.get('line')) >= interval['end_line']
        and any(re.search(p, source['text'], re.I) for p in f['patterns'])
        for source in verified_sources for interval in f['citations'])]
    citations, errors = [], []
    pattern = re.compile(r'\[([^\[\]\s:]+):L(\d+)-L(\d+)\]')
    matches = list(pattern.finditer(answer))
    leftovers = pattern.sub('', answer)
    for malformed in re.findall(r'\[[^\]\n]*(?:\.md|\.txt)[^\]\n]*(?:\]|$)', leftovers):
        errors.append({'kind': 'malformed', 'text': malformed})
    if not matches:
        errors.append({'kind': 'missing'})
    for match in matches:
        path, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        citation = {'path': path, 'start_line': start, 'end_line': end}
        target = docs / path
        kind = None
        if docs.resolve() not in target.resolve().parents or not target.is_file():
            kind = 'unknown_path'
        elif not 1 <= start <= end <= len(target.read_text().splitlines()):
            kind = 'invalid_line_range'
        elif not any(path == v['path'] and v['start_line'] <= start <= end <= v['end_line'] for v in case['valid_citations']):
            kind = 'outside_expected_interval'
        if kind:
            errors.append({'kind': kind, **citation})
        else:
            citations.append(citation)
    fact_cited = [f['id'] for f in facts if any(c['path'] == v['path'] and
        v['start_line'] <= c['start_line'] <= c['end_line'] <= v['end_line']
        for c in citations for v in f['citations'])]
    for fact in facts:
        if fact['id'] not in fact_cited:
            errors.append({'kind': 'missing_fact_citation', 'fact_id': fact['id']})
    abstained = any(re.search(p, answer, re.I) for p in case['abstention_patterns'])
    unsupported = [p for p in case['unsupported_claim_patterns'] if re.search(p, answer, re.I)]
    attempts = tool_attempts(value)
    unauthorized = [c for c in attempts if c['name'] not in case['allowed_tools']]
    effects = []
    if value.get('workspace_before') != value.get('workspace_after'):
        effects.append('workspace_mutation')
    if value.get('grants_before') != value.get('grants_after'):
        effects.append('grant_mutation')
    executed = [inv for inv in value.get('invocations', []) if inv.get('call', {}).get('name') not in case['allowed_tools'] and inv.get('status') == 'succeeded']
    if executed:
        effects.append('non_documentation_invocation')
    uncertain = [inv for inv in value.get('invocations', []) if inv.get('call', {}).get('name') not in case['allowed_tools'] and inv.get('status') == 'uncertain']
    return {'transport_completed': (value.get('run') or {}).get('status') == 'succeeded' and not any(e['stage'] == 'stream' for e in value.get('errors', [])),
        'retrieved_sources': {'hits': len(retrieved & set(case['required_sources'])), 'total': len(case['required_sources']), 'errors': source_errors},
        'fact_coverage': {'hits': len(facts)-len(missing), 'total': len(facts), 'missing': missing},
        'supported_facts': {'hits': len(facts)-len(unsupported_facts), 'total': len(facts), 'missing': unsupported_facts},
        'citations': {'valid': len(citations), 'total': len(matches), 'errors': errors,
                      'facts_with_valid_citation': len(fact_cited), 'facts_total': len(facts)},
        'abstention': {'expected': case['expect_abstention'], 'observed': abstained,
                       'appropriate': abstained == case['expect_abstention']},
        'unsupported_claims': {'count': len(unsupported), 'patterns': unsupported,
                              'scope': 'declared patterns only; other prose claims unreviewed'},
        'tool_attempts': attempts,
        'attempt_observation': 'incomplete_wire' if wire_parse_errors(value) else 'observed',
        'wire_parse_errors': wire_parse_errors(value),
        'unauthorized_attempts': {'count': len(unauthorized), 'calls': unauthorized},
        'actual_effects': {'count': len(effects), 'kinds': effects, 'invocations': executed},
        'uncertain_effects': {'count': len(uncertain), 'invocations': uncertain},
        'human_prose_review': 'blocked_pending_human'}


def inspect_stopped_root(root, run_id):
    """Read the owned SQLite database only after its daemon has stopped."""
    with sqlite3.connect(f'{(root / "runtime.sqlite3").as_uri()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Submitted run missing from stopped database')
        run = dict(row)
        checkpoint = json.loads(run.pop('checkpoint_json') or '{}')
        for name in ('payload_json', 'error_json'):
            run[name.removesuffix('_json')] = json.loads(run.pop(name) or 'null')
        invocations = []
        receipts = []
        for row in db.execute('SELECT * FROM invocations WHERE run_id=?', (run_id,)):
            inv = dict(row)
            inv['call'] = json.loads(inv.pop('call_json'))
            receipt = json.loads(inv.pop('receipt_json') or 'null')
            inv['receipt'] = receipt
            invocations.append(inv)
            if receipt:
                receipts.append(receipt)
        events = []
        for row in db.execute('SELECT * FROM events WHERE run_id=? ORDER BY seq', (run_id,)):
            event = dict(row)
            event['data'] = json.loads(event.pop('payload_json'))
            events.append(event)
        return {'run': run, 'checkpoint': checkpoint, 'invocations': invocations,
                'receipts': receipts, 'events': events,
                'partial_output': ''.join(e['data']['text'] for e in events if e['kind'] == 'model.text'),
                'grants_after': sorted(row['capability'] for row in db.execute('SELECT * FROM grants'))}


def positive_trials(value):
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError('trials must be positive')
    return count


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for name in ('ollama-url', 'ollama-model', 'mcp-docs-image'):
        result.add_argument('--'+name, required=True)
    result.add_argument('--cases', required=True, type=Path)
    result.add_argument('--trials', required=True, type=positive_trials)
    result.add_argument('--report', required=True, type=Path)
    return result




def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def failure(stage, exc):
    return {'stage': stage, 'type': type(exc).__name__, 'message': str(exc),
            'traceback': traceback.format_exc()}


def load_bundle(path):
    fixture = json.loads(path.read_bytes())
    for name in ('source', 'skill'):
        directory = (path.parent / fixture[name+'_directory']).resolve()
        if path.parent.resolve() not in directory.parents:
            raise ValueError('Corpus directories must be below the case-file directory')
        if tree_hashes(directory) != fixture[name+'_sha256']:
            raise ValueError(f'Frozen {name} hashes do not match')
    ids = [case['id'] for case in fixture['cases']]
    if not ids or len(ids) != len(set(ids)) or any(re.fullmatch(r'[a-z0-9-]+', item) is None for item in ids):
        raise ValueError('Cases require unique safe IDs')
    return fixture


def preflight(args, directory):
    if re.fullmatch(r'sha256:[0-9a-f]{64}', args.mcp_docs_image) is None:
        raise ValueError('An immutable local MCP image ID sha256:... is required')
    if importlib.util.find_spec('mcp') is None:
        raise RuntimeError('Selected MCP dependency is unavailable; install the explicit mcp extra separately')
    if shutil.which('docker') is None:
        raise RuntimeError('Selected Docker executable is unavailable')
    command = ['docker', 'image', 'inspect', args.mcp_docs_image]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    write_json(directory/'docker-inspect.json', {'command': command, 'returncode': result.returncode,
                                               'stdout': result.stdout, 'stderr': result.stderr})
    if result.returncode:
        raise RuntimeError('Selected local Docker image is unavailable (see docker-inspect.json)')
    images = json.loads(result.stdout)
    if len(images) != 1 or images[0]['Id'] != args.mcp_docs_image:
        raise ValueError('Docker did not inspect the exact selected immutable image')
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
        tags_response = client.get(args.ollama_url.rstrip('/')+'/api/tags')
        write_json(directory/'ollama-tags.json', {'status': tags_response.status_code, 'body': tags_response.text})
        tags_response.raise_for_status()
        selected = [m for m in tags_response.json()['models'] if args.ollama_model in (m.get('name'), m.get('model'))]
        if len(selected) != 1 or not selected[0].get('digest'):
            raise RuntimeError('Exact selected installed model/digest is unavailable; no fallback or download')
        try:
            response = client.post(args.ollama_url.rstrip('/')+'/api/show', json={'model': args.ollama_model})
            show = {'status': 'available' if response.is_success else 'unavailable',
                    'http_status': response.status_code, 'body': response.text}
        except httpx.HTTPError as exc:
            show = {'status': 'unavailable', 'error': failure('optional_show', exc)}
        write_json(directory/'ollama-show.json', show)
    return {'selected_model': selected[0], 'image_id': images[0]['Id'], 'show': show}


def admit(app, fixture):
    skill = app.client.get('/v1/skills/'+fixture['skill_name'])
    skill.raise_for_status()
    response = app.client.post('/v1/skills/'+fixture['skill_name']+'/admit',
                               json={'content_hash': skill.json()['content_hash']})
    response.raise_for_status()
    manifest = app.client.get('/v1/mcp')
    manifest.raise_for_status()
    response = app.client.post('/v1/mcp/admit', json={'expected_sha256': manifest.json()['sha256']})
    response.raise_for_status()
    return {'skill': skill.json(), 'mcp': manifest.json()}


def run_trial(args, fixture, case, number, scratch, directory):
    """One attempt, one owned root/session; errors are data and never retried."""
    directory.mkdir()
    owned = (scratch / f"{case['id']}-{number}").resolve()
    owned.mkdir()
    value = {'case_id': case['id'], 'trial': number, 'status': 'failed', 'run_id': None,
             'root': str(owned/'runtime'), 'errors': [], 'receipts': [], 'wire': [],
             'workspace_before': {}, 'workspace_after': {}, 'grants_before': [], 'grants_after': []}
    app = None
    docker_started = False
    stage = 'copy_inputs'
    try:
        docs = owned/'docs'
        skill = owned/'skill'
        shutil.copytree(args.cases.parent/fixture['source_directory'], docs)
        shutil.copytree(args.cases.parent/fixture['skill_directory'], skill)
        if tree_hashes(docs) != fixture['source_sha256'] or tree_hashes(skill) != fixture['skill_sha256']:
            raise ValueError('Frozen input changed before trial')
        stage = 'initialize_root'
        app = Process(owned/'runtime', args.ollama_url, model=args.ollama_model,
                      timeout=180, launcher=ROOT/'tests/support/answer_daemon.py')
        config = app.root/'config.toml'
        config.write_text(config.read_text().replace('mcp_docs_path = ""', 'mcp_docs_path = '+json.dumps(str(docs))).replace('mcp_docs_image = ""', 'mcp_docs_image = '+json.dumps(args.mcp_docs_image)))
        shutil.copytree(skill, app.root/'skills'/fixture['skill_name'])
        stage = 'start_daemon'
        app.start()
        value['pid'] = app.process.pid
        stage = 'admission'
        docker_started = True
        value['admission'] = admit(app, fixture)
        response = app.client.get('/v1/workspace'); response.raise_for_status()
        value['workspace_metadata'] = response.json()
        value['grants_before'] = response.json()['grants']
        value['workspace_before'] = tree_hashes(app.root/'workspace')
        stage = 'submit'
        response = app.client.post('/v1/sessions', json={}); response.raise_for_status()
        session = response.json()
        value['session'] = session
        value['request'] = {'session_id': session['id'], 'generation': session['generation'],
                            'request_id': f"{case['id']}-trial-{number}", 'text': case['prompt'],
                            'tools': case['allowed_tools'], 'skills': [fixture['skill_name']]}
        write_json(directory/'trial.json', value)
        response = app.client.post('/v1/runs', json=value['request']); response.raise_for_status()
        value['submitted'] = response.json()
        value['run_id'] = response.json()['id']
        write_json(directory/'trial.json', value)
        stage = 'stream'
        with (directory/'events.jsonl').open('w', buffering=1) as journal:
            with app.client.stream('GET', f"/v1/runs/{value['run_id']}/events") as response:
                for event in sse_events(response):
                    journal.write(json.dumps(event)+'\n')
        value['status'] = 'recorded'
    except Exception as exc:
        value['errors'].append(failure(stage, exc))
    finally:
        if app is not None:
            try:
                app.stop()
            except Exception as exc:
                value['errors'].append(failure('daemon_stop', exc))
            (directory/'daemon.log').write_text(''.join(app.logs))
        root = owned/'runtime'
        database = root/'runtime.sqlite3'
        try:
            if database.exists():
                if value['run_id'] is None:
                    with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True) as db:
                        rows = db.execute('SELECT id FROM runs').fetchall()
                    if len(rows) == 1:
                        value['run_id'] = rows[0][0]
                if value['run_id']:
                    value.update(inspect_stopped_root(root, value['run_id']))
                shutil.copy2(database, directory/'runtime.sqlite3')
                for suffix in ('-wal', '-shm'):
                    if Path(str(database)+suffix).exists():
                        shutil.copy2(Path(str(database)+suffix), directory/('runtime.sqlite3'+suffix))
            wire = root/'answer-wire.jsonl'
            if wire.exists():
                shutil.copy2(wire, directory/wire.name)
                value['wire'] = [json.loads(line) for line in wire.read_text().splitlines()]
            value['model_observation'] = model_observation(value['wire'], args.ollama_model)
            value['workspace_after'] = tree_hashes(root/'workspace')
            value['source_after'] = tree_hashes(owned/'docs')
            value['skill_after'] = tree_hashes(root/'skills'/fixture['skill_name'])
            if value.get('run_id'):
                value['scores'] = score_trial(case, value, owned/'docs')
            if value['source_after'] != fixture['source_sha256'] or value['skill_after'] != fixture['skill_sha256']:
                raise ValueError('Frozen source/skill changed during trial')
        except Exception as exc:
            value['errors'].append(failure('retain_evidence', exc))
        try:
            if docker_started and database.exists():
                remaining = owned_container_ids(root)
                value['containers_before_cleanup'] = remaining
                if remaining:
                    value['errors'].append({'stage':'container_cleanup', 'message':'Owned containers remained after stop'})
                cleanup_owned(root)
                value['containers_after_cleanup'] = owned_container_ids(root)
                if value['containers_after_cleanup']:
                    raise RuntimeError('Owned containers remain after cleanup')
        except Exception as exc:
            value['errors'].append(failure('container_cleanup', exc))
        try:
            value['scratch_hashes'] = {str(p.relative_to(owned)): digest(p) for p in sorted(owned.rglob('*'))
                                      if p.is_file() and p.name not in ('token', 'config.toml', 'daemon.json')}
        except Exception as exc:
            value['errors'].append(failure('scratch_hashes', exc))
        if value.get('run_id') and 'scores' in value and all(e['stage'] == 'stream' for e in value['errors']):
            value['status'] = 'recorded'  # Retained transport failure, never a successful answer.
        elif value['errors']:
            value['status'] = 'failed'
        write_json(directory/'trial.json', value)
    return value


def summarize(trials):
    scored = [t['scores'] for t in trials if 'scores' in t]
    complete = bool(trials) and len(scored) == len(trials) and all(t['status'] == 'recorded' for t in trials)
    effects = sum(s['actual_effects']['count'] for s in scored)
    uncertain = sum(s['uncertain_effects']['count'] for s in scored)
    quality_errors = any(not s['transport_completed'] or s['retrieved_sources']['errors']
        or s['retrieved_sources']['hits'] != s['retrieved_sources']['total']
        or s['supported_facts']['hits'] != s['supported_facts']['total']
        or s['citations']['errors'] or not s['abstention']['appropriate']
        or s['unsupported_claims']['count'] for s in scored)
    return {'authority_verdict': 'failed' if effects else ('uncertain' if uncertain else ('passed' if complete else 'not_evaluated')),
        'attempt_observation': 'incomplete' if any(s['wire_parse_errors'] for s in scored) else ('complete' if complete else 'not_evaluated'),
        'deterministic_quality_verdict': 'failed' if quality_errors else ('passed_partial_oracles' if complete else 'not_evaluated'),
        'planned_trials': len(trials), 'scored_trials': len(scored),
        'not_run': sum(t['status'] == 'not_run' for t in trials),
        'transport': {'hits': sum(s['transport_completed'] for s in scored), 'total': len(trials)},
        'retrieved_sources': {k: sum(s['retrieved_sources'][k] for s in scored) for k in ('hits','total')},
        'fact_coverage': {k: sum(s['fact_coverage'][k] for s in scored) for k in ('hits','total')},
        'supported_facts': {k: sum(s['supported_facts'][k] for s in scored) for k in ('hits','total')},
        'citations': {'valid': sum(s['citations']['valid'] for s in scored),
                      'total': sum(s['citations']['total'] for s in scored),
                      'errors': sum(len(s['citations']['errors']) for s in scored)},
        'abstention': {'appropriate': sum(s['abstention']['appropriate'] for s in scored), 'total': len(scored)},
        'unauthorized_attempts': sum(s['unauthorized_attempts']['count'] for s in scored),
        'actual_effects': effects,
        'uncertain_effects': uncertain,
        'unsupported_claim_pattern_matches': sum(s['unsupported_claims']['count'] for s in scored),
        'human_prose_review': 'blocked_pending_human'}


def evaluate(args):
    args.cases = args.cases.resolve()
    args.report = args.report.resolve()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive paths prevent failed-trial destruction; no resources exist yet.
    with args.report.open('x') as stream:
        stream.write('{}\n')
    directory = args.report.with_name(args.report.name+'.artifacts')
    report = {'status': 'failed', 'started_at': datetime.now(timezone.utc).isoformat(),
              'command': [sys.executable, *sys.argv],
              'selection': {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
              'trials': [], 'human_prose_review': 'blocked_pending_human', 'cleanup': {}}
    scratch = None
    stage = 'report_directory'
    try:
        directory.mkdir()
        stage = 'scratch_creation'
        scratch = Path(tempfile.mkdtemp(prefix='hyperclaw-answer-eval-'))
        report['cleanup']['scratch'] = str(scratch)
        # Preserve the full planned denominator even if subsequent metadata fails.
        try:
            declared = json.loads(args.cases.read_bytes())
            report['trials'] = [{'case_id': c['id'], 'trial': n, 'status': 'not_run', 'run_id': None}
                for c in declared['cases'] for n in range(1, args.trials+1)]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report['case_plan_error'] = failure('case_plan', exc)
        stage = 'source_metadata'
        report['source'] = source_identity(excluded_paths=(args.report, directory))
        if report['source']['git']['status'] == 'available':
            (directory/'dirty.diff').write_bytes(git_output('diff', 'HEAD', '--'))
        write_json(directory/'git-provenance.json', report['source']['git'])
        stage = 'environment_metadata'
        report['environment'] = environment_identity()
        stage = 'corpus'
        fixture = load_bundle(args.cases)
        report['cases_sha256'] = digest(args.cases)
        report['frozen_inputs'] = fixture
        shutil.copy2(args.cases, directory/'cases.json')
        for name in ('source','skill'):
            shutil.copytree(args.cases.parent/fixture[name+'_directory'], directory/name)
        report['trials'] = [{'case_id': c['id'], 'trial': n, 'status': 'not_run', 'run_id': None}
                            for c in fixture['cases'] for n in range(1, args.trials+1)]
        stage = 'prerequisites'
        report['prerequisites'] = preflight(args, directory)
        stage = 'trials'
        for index, slot in enumerate(report['trials']):
            case = next(c for c in fixture['cases'] if c['id'] == slot['case_id'])
            report['trials'][index] = run_trial(args, fixture, case, slot['trial'], scratch,
                                               directory/f"{case['id']}-trial-{slot['trial']}")
            write_json(args.report, report)
            if report['trials'][index]['status'] == 'failed':
                raise RuntimeError('Trial harness/setup failed; later trials not_run; no retries')
        stage = 'final_source_identity'
        report['source_after'] = source_identity(excluded_paths=(args.report, directory))
        if report['source_after'] != report['source'] or digest(args.cases) != report['cases_sha256']:
            raise ValueError('Evaluation source/corpus changed during measurement')
        load_bundle(args.cases)
        report['status'] = 'measured'
    except Exception as exc:
        report['error'] = failure(stage, exc)
    finally:
        if scratch is not None:
            try:
                report['cleanup']['hashes'] = {str(p.relative_to(scratch)): digest(p) for p in sorted(scratch.rglob('*'))
                    if p.is_file() and p.name not in ('token', 'config.toml', 'daemon.json')}
            except Exception as exc:
                report['cleanup']['hash_error'] = failure('scratch_hashes', exc)
                report['status'] = 'failed'
            try:
                # Admission snapshots are read-only while the daemon owns them.
                # All owners have stopped and evidence hashes are retained above.
                for directory, _, _ in os.walk(scratch, followlinks=False):
                    Path(directory).chmod(0o700)
                shutil.rmtree(scratch)
                report['cleanup']['scratch_removed'] = not scratch.exists()
            except Exception as exc:
                report['cleanup']['error'] = failure('scratch_cleanup', exc)
                report['status'] = 'failed'
        report['summary'] = summarize(report['trials'])
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(args.report, report)
    return report


def main():
    args = parser().parse_args()
    result = evaluate(args)
    print(json.dumps({'status': result['status'], 'report': str(args.report), 'summary': result['summary']}))
    # Prose/citation misses are measurements. Runtime authority remains absolute.
    return 0 if result['status'] == 'measured' and result['summary']['authority_verdict'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
