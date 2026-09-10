#!/usr/bin/env python3
"""Run the frozen lexical-memory fixture against real temporary Stores."""
import argparse
import asyncio
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import time

from hyperclaw.contracts import MemoryScope
from hyperclaw.memory import Memory
from hyperclaw.store import Store


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/evaluations/memory_cases.json'


class ControlledClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


async def evaluate_case(case, observed_at):
    clock = ControlledClock(observed_at)
    with tempfile.TemporaryDirectory(prefix='hyperclaw-memory-eval-') as root:
        store = await Store.open(Path(root))
        try:
            memory = Memory(store, clock=clock)
            aliases = {
                item['session']
                for item in case['records']
                if item.get('session') is not None
            }
            query_alias = case['query_scope'].get('session')
            if query_alias is not None:
                aliases.add(query_alias)
            sessions = {alias: (await store.create_session()).id for alias in sorted(aliases)}

            def scope(values):
                alias = values.get('session')
                return MemoryScope(
                    workspace_id=f"fixture-workspace-{values['workspace']}",
                    session_id=sessions[alias] if alias is not None else None,
                )

            records = {}
            id_to_key = {}
            states = {}
            for item in case['records']:
                valid_until = None
                if item.get('valid_for_seconds') is not None:
                    valid_until = clock() + timedelta(seconds=item['valid_for_seconds'])
                record = await memory.remember(scope(item), item['text'], valid_until=valid_until)
                records[item['key']] = record
                id_to_key[record.id] = item['key']
                states[item['key']] = {'status': 'active', 'valid_until': valid_until}

            for action in case['actions']:
                if action['op'] == 'advance':
                    clock.advance(action['seconds'])
                    continue
                target = records[action['target']]
                if action['op'] == 'correct':
                    valid_until = None
                    if action.get('valid_for_seconds') is not None:
                        valid_until = clock() + timedelta(seconds=action['valid_for_seconds'])
                    record = await memory.correct(
                        target.id, action['text'], target.scope, valid_until=valid_until)
                    records[action['key']] = record
                    id_to_key[record.id] = action['key']
                    states[action['target']]['status'] = 'superseded'
                    states[action['key']] = {'status': 'active', 'valid_until': valid_until}
                elif action['op'] == 'forget':
                    await memory.forget(target.id, target.scope)
                    states[action['target']]['status'] = 'forgotten'
                else:
                    raise ValueError(f"Unknown fixture action: {action['op']}")

            returned = await memory.search(scope(case['query_scope']), case['query'], limit=5)
            returned_keys = [id_to_key[item.id] for item in returned]
            expected = case['expected_keys']
            forbidden = case['forbidden_keys']
            obsolete = [
                key for key, state in states.items()
                if state['status'] != 'active'
                or (state['valid_until'] is not None and state['valid_until'] <= clock())
            ]
            return {
                'id': case['id'],
                'category': case['category'],
                'returned_keys': returned_keys,
                'expected_keys': expected,
                'forbidden_keys': forbidden,
                'missing_keys': [key for key in expected if key not in returned_keys],
                'forbidden_returned_keys': [key for key in forbidden if key in returned_keys],
                'obsolete_keys': obsolete,
                'stale_returned_keys': [key for key in obsolete if key in returned_keys],
            }
        finally:
            await store.close()


def safety_counts(cases):
    return (
        sum(len(case['forbidden_returned_keys']) for case in cases),
        sum(len(case['stale_returned_keys']) for case in cases),
    )


async def evaluate():
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    observed_at = datetime.fromisoformat(fixture['observed_at'].replace('Z', '+00:00'))
    started = time.monotonic()
    cases = [await evaluate_case(case, observed_at) for case in fixture['cases']]

    def recall(category):
        selected = [case for case in cases if case['category'] == category]
        hits = sum(not case['missing_keys'] for case in selected)
        return {'hits': hits, 'total': len(selected), 'score': hits / len(selected)}

    forbidden_count, stale_count = safety_counts(cases)
    return {
        'fixture_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
        'total_cases': len(cases),
        'category_counts': {
            category: sum(case['category'] == category for case in cases)
            for category in ('exact', 'paraphrase', 'revision', 'scope')
        },
        'exact_recall_at_5': recall('exact'),
        'paraphrase_recall_at_5': recall('paraphrase'),
        'forbidden_return_count': forbidden_count,
        'stale_return_count': stale_count,
        'elapsed_seconds': round(time.monotonic() - started, 6),
        'cases': cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(evaluate())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'cases'}, sort_keys=True))
    exact = report['exact_recall_at_5']['score'] == 1.0
    safe = report['forbidden_return_count'] == report['stale_return_count'] == 0
    raise SystemExit(0 if exact and safe else 1)


if __name__ == '__main__':
    main()
