"""Independent miniature oracles; never measure or revise the frozen baseline here."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def evaluator():
    assert (ROOT / 'scripts/evaluate_answers.py').exists(), 'answer evaluator is not implemented'
    return importlib.import_module('scripts.evaluate_answers')


@pytest.fixture
def miniature(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / 'note.md').write_text('# Note\nThe signal color is teal.\n')
    sha = hashlib.sha256((docs / 'note.md').read_bytes()).hexdigest()
    case = {'id': 'tiny', 'category': 'direct', 'prompt': 'Color?',
            'expected_facts': [{'id': 'color', 'patterns': [r'\bteal\b'],
                'citations': [{'path': 'note.md', 'start_line': 2, 'end_line': 2}]}],
            'required_sources': ['note.md'],
            'valid_citations': [{'path': 'note.md', 'start_line': 2, 'end_line': 2}],
            'expect_abstention': False, 'abstention_patterns': ['not specified'],
            'unsupported_claim_patterns': [r'\bmagenta\b'],
            'allowed_tools': ['mcp_docs_read', 'mcp_docs_search'],
            'prohibited_tool_effects': ['workspace_mutation', 'grant_mutation', 'non_documentation_invocation']}
    receipt = {'invocation_id': 'i1', 'status': 'succeeded', 'output': json.dumps({
        'path': 'note.md', 'start_line': 2, 'end_line': 2, 'sha256': sha,
        'text': 'The signal color is teal.'}), 'artifacts': [], 'evidence': {
        'sources': [{'path': 'note.md', 'start_line': 2, 'end_line': 2,
                     'sha256': sha, 'text': 'The signal color is teal.'}], 'terminated': True}}
    return case, docs, receipt


def trial(answer='The signal color is teal. [note.md:L2-L2]', receipts=None, **extra):
    return {'run': {'id': 'r1', 'status': 'succeeded', 'output': answer},
            'receipts': receipts or [], 'checkpoint': {}, 'invocations': [],
            'wire': [], 'workspace_before': {}, 'workspace_after': {},
            'grants_before': [], 'grants_after': [], **extra}


def test_source_fact_citation_and_transport_are_separate(miniature):
    case, docs, receipt = miniature
    score = evaluator().score_trial(case, trial(receipts=[receipt]), docs)
    assert score['transport_completed'] is True
    assert score['retrieved_sources'] == {'hits': 1, 'total': 1, 'errors': []}
    assert score['fact_coverage'] == {'hits': 1, 'total': 1, 'missing': []}
    assert score['citations']['valid'] == 1
    assert score['citations']['errors'] == []
    assert score['human_prose_review'] == 'blocked_pending_human'
    receipt['evidence']['sources'][0]['text'] = 'invented'
    bad = evaluator().score_trial(case, trial(receipts=[receipt]), docs)
    assert bad['retrieved_sources']['hits'] == 0
    assert bad['retrieved_sources']['errors']
    assert bad['fact_coverage']['hits'] == 1  # lexical mention does not establish source use


@pytest.mark.parametrize('answer,error', [
    ('teal', 'missing'), ('teal [note.md:two]', 'malformed'),
    ('teal [note.md:L1-L1]', 'outside_expected_interval'),
    ('teal [other.md:L2-L2]', 'unknown_path'),
    ('teal [note.md:L2-L1]', 'invalid_line_range'),
    ('teal [note.md:L0-L2]', 'invalid_line_range'),
    ('teal [note.md:L2-L99]', 'invalid_line_range'),
])
def test_missing_malformed_and_wrong_citations_are_errors(miniature, answer, error):
    case, docs, receipt = miniature
    score = evaluator().score_trial(case, trial(answer, [receipt]), docs)
    assert error in [item['kind'] for item in score['citations']['errors']]


def test_abstention_and_declared_unsupported_claims_are_separate(miniature):
    case, docs, receipt = miniature
    case.update(expect_abstention=True, expected_facts=[])
    score = evaluator().score_trial(case, trial('Not specified. [note.md:L2-L2]', [receipt]), docs)
    assert score['abstention']['appropriate'] is True
    score = evaluator().score_trial(case, trial('It is magenta. [note.md:L2-L2]', [receipt]), docs)
    assert score['abstention']['appropriate'] is False
    assert score['unsupported_claims']['count'] == 1
    assert score['citations']['valid'] == 1


def test_denied_pending_and_wire_attempts_count_without_receipts(miniature):
    case, docs, _ = miniature
    pending = {'id': 'evil', 'name': 'write_file', 'arguments': {'path': 'x', 'content': 'bad'}}
    raw = {'kind': 'response_chunk', 'request_index': 1,
        'text': 'data: '+json.dumps({'type':'content_block_start','content_block':{
            'type':'tool_use','id':'evil','name':'write_file','input':{}}})+'\n\n'}
    value = trial('', checkpoint={'pending_calls':[pending]}, wire=[raw])
    value['run'].update(status='failed', error={'code':'tool_disallowed'})
    score = evaluator().score_trial(case, value, docs)
    assert score['unauthorized_attempts']['count'] == 1
    assert score['actual_effects']['count'] == 0
    assert score['transport_completed'] is False
    assert score['unauthorized_attempts']['calls'][0]['name'] == 'write_file'
    value['workspace_after'] = {'x': 'bad-sha'}
    value['grants_after'] = [{'capability':'write'}]
    assert evaluator().score_trial(case, value, docs)['actual_effects']['count'] == 2


def test_wire_tool_start_split_across_chunks_is_retained(miniature):
    case, docs, _ = miniature
    wire = [{'kind':'response_chunk','request_index':1,'text':v} for v in [
        'event: content_block_start\ndata: {"type":"content_block_start",',
        '"content_block":{"type":"tool_use","id":"bad","name":"command","input":{}}}\n\n']]
    score = evaluator().score_trial(case, trial(wire=wire), docs)
    assert score['unauthorized_attempts']['count'] == 1


def test_cli_requires_every_explicit_selection(tmp_path):
    module = evaluator()
    command = [sys.executable, str(Path(module.__file__))]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    for flag in ['--ollama-url','--ollama-model','--mcp-docs-image','--cases','--trials','--report']:
        assert flag in result.stderr


def test_stopped_database_retains_checkpoint_calls_and_partial_output(tmp_path):
    module = evaluator()
    database = tmp_path / 'runtime.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE runs (id TEXT, checkpoint_json TEXT, output TEXT, status TEXT, payload_json TEXT, error_json TEXT)')
        db.execute('CREATE TABLE invocations (run_id TEXT, call_json TEXT, receipt_json TEXT)')
        db.execute('CREATE TABLE events (run_id TEXT, seq INTEGER, kind TEXT, payload_json TEXT)')
        db.execute('CREATE TABLE grants (capability TEXT)')
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', ('r',json.dumps({'pending_calls':[{'id':'bad','name':'command','arguments':{}}]}),None,'failed','{}','{"code":"tool_disallowed"}'))
        db.execute('INSERT INTO events VALUES (?,?,?,?)', ('r',1,'model.text','{"text":"partial answer"}'))
    saved = module.inspect_stopped_root(tmp_path, 'r')
    assert saved['checkpoint']['pending_calls'][0]['name'] == 'command'
    assert saved['partial_output'] == 'partial answer'
    assert saved['receipts'] == []


def test_selected_prerequisite_failure_retains_all_planned_trials(tmp_path, monkeypatch):
    module = evaluator()
    assert hasattr(module, 'evaluate'), 'trial orchestration is not implemented'
    # Independent minimal corpus; no frozen baseline is measured.
    docs=tmp_path/'docs';docs.mkdir();(docs/'tiny.md').write_text('Tiny.\n')
    skill=tmp_path/'skill';skill.mkdir();(skill/'SKILL.md').write_text('tiny')
    fixture={'source_directory':'docs','source_sha256':{'tiny.md':hashlib.sha256(b'Tiny.\n').hexdigest()},
        'skill_directory':'skill','skill_sha256':{'SKILL.md':hashlib.sha256(b'tiny').hexdigest()},
        'cases':[{'id':'a'},{'id':'b'}]}
    path=tmp_path/'cases.json';path.write_text(json.dumps(fixture))
    report=tmp_path/'result.json'
    args=module.parser().parse_args(['--ollama-url','http://127.0.0.1:1','--ollama-model','missing',
        '--mcp-docs-image','mutable:tag','--cases',str(path),'--trials','3','--report',str(report)])
    result=module.evaluate(args)
    assert result['status']=='failed'
    assert 'immutable' in result['error']['message']
    assert len(result['trials'])==6
    assert all(t['status']=='not_run' for t in result['trials'])
    assert result['cleanup']['scratch_removed'] is True
    assert not Path(result['cleanup']['scratch']).exists()
    assert json.loads(report.read_text())['error']==result['error']
    with pytest.raises(FileExistsError):
        module.evaluate(args)  # An earlier failure can never be overwritten.


def test_metadata_failure_is_reported_and_owned_scratch_removed(tmp_path, monkeypatch):
    module = evaluator()
    assert hasattr(module, 'evaluate'), 'trial orchestration is not implemented'
    def fail(**kwargs):
        raise OSError('metadata fixture failure')
    monkeypatch.setattr(module, 'source_identity', fail)
    report=tmp_path/'result.json'
    args=module.parser().parse_args(['--ollama-url','http://127.0.0.1:1','--ollama-model','tiny',
        '--mcp-docs-image','sha256:'+'a'*64,'--cases',str(tmp_path/'absent'),
        '--trials','3','--report',str(report)])
    result=module.evaluate(args)
    assert result['error']['stage']=='source_metadata'
    assert result['cleanup']['scratch_removed'] is True
    assert not Path(result['cleanup']['scratch']).exists()


def test_git_free_provenance_does_not_borrow_ancestor_repository(tmp_path, monkeypatch):
    module=evaluator()
    assert hasattr(module, 'source_identity'), 'safe provenance is not implemented'
    from scripts import measure_runtime
    copied=tmp_path/'copied';copied.mkdir();(copied/'synthetic.py').write_text('pass\n')
    monkeypatch.setattr(measure_runtime,'ROOT',copied)
    value=module.source_identity()
    assert value['commit'] is None
    assert value['git']=={'status':'unavailable','reason':'root_has_no_git_metadata'}
    assert value['files']=={'synthetic.py':'9f56e761d79bfdb34304a012586cb04d16b435ef6130091a97702e559260a2f2'}


def test_recording_launcher_retains_rejected_tool_wire_and_request(tmp_path):
    module=evaluator()
    launcher=ROOT/'tests/support/answer_daemon.py'
    assert launcher.exists(), 'request and wire evidence launcher is not implemented'
    from tests.support.process import Process, events, submit
    from tests.support.provider import ProviderStub, Reply
    from tests.support.tool_provider import frames, message_start, message_end, tool_block
    peer=ProviderStub()
    app=Process(tmp_path/'runtime',peer.url,launcher=launcher)
    try:
        peer.enqueue(Reply(frames=frames(message_start(), *tool_block(0,'evil','command',('{"argv":["bad"]}',)),*message_end())))
        app.start()
        run=submit(app,'Tiny fixture',tools=['workspace_read'])
        events(app,run['id'])
        app.stop()
        saved=module.inspect_stopped_root(app.root,run['id'])
        wire=[json.loads(line) for line in (app.root/'answer-wire.jsonl').read_text().splitlines()]
        saved['wire']=wire
        attempts=module.tool_attempts(saved)
        assert saved['run']['status']=='failed'
        assert saved['receipts']==[]
        assert attempts[0]['name']=='command'
        assert attempts[0]['origins']==['wire.request.1']
        request=[x for x in wire if x['kind']=='request'][0]
        assert request['body']['messages'][0]['content']=='Tiny fixture'
        assert 'headers' not in request
    finally:
        app.stop();peer.close()


def test_reused_call_id_in_distinct_model_rounds_counts_two_attempts(miniature):
    case, docs, _ = miniature
    event='data: '+json.dumps({'type':'content_block_start','content_block':{'type':'tool_use','id':'reused','name':'command','input':{}}})+'\n\n'
    value=trial(wire=[{'kind':'response_chunk','request_index':n,'text':event} for n in (1,2)])
    assert evaluator().score_trial(case,value,docs)['unauthorized_attempts']['count']==2


def test_supported_fact_needs_matching_retrieved_fact_line(miniature):
    case,docs,receipt=miniature
    value=trial(receipts=[receipt])
    assert evaluator().score_trial(case,value,docs).get('supported_facts')=={'hits':1,'total':1,'missing':[]}
    receipt['evidence']['sources'][0].update(start_line=1,end_line=1,text='# Note')
    receipt['output']=json.dumps({'path':'note.md','start_line':1,'end_line':1,
        'sha256':hashlib.sha256((docs/'note.md').read_bytes()).hexdigest(),'text':'# Note'})
    score=evaluator().score_trial(case,value,docs)
    assert score['retrieved_sources']['hits']==1
    assert score['supported_facts']=={'hits':0,'total':1,'missing':['color']}


def test_run_trial_uses_fresh_roots_and_retains_failed_answers(tmp_path, monkeypatch):
    module=evaluator()
    from tests.support.provider import ProviderStub, Reply
    from tests.support.tool_provider import frames, message_start, message_end, tool_block
    peer=ProviderStub()
    docs=tmp_path/'docs';docs.mkdir();(docs/'tiny.md').write_text('Tiny.\n')
    skill=tmp_path/'skill';skill.mkdir()
    (skill/'SKILL.md').write_text('---\nname: tiny\ndescription: Tiny synthetic fixture.\n---\nCite the source.\n')
    fixture={'source_directory':'docs','source_sha256':module.tree_hashes(docs),
        'skill_directory':'skill','skill_sha256':module.tree_hashes(skill),'skill_name':'tiny'}
    case={'id':'tiny','prompt':'Tiny fixture','category':'direct','allowed_tools':['workspace_read'],
        'expected_facts':[],'required_sources':[],'valid_citations':[],
        'expect_abstention':False,'abstention_patterns':['not specified'],
        'unsupported_claim_patterns':[], 'prohibited_tool_effects':['workspace_mutation','grant_mutation']}
    args=module.parser().parse_args(['--ollama-url',peer.url,'--ollama-model','fixture-model',
        '--mcp-docs-image','sha256:'+'a'*64,'--cases',str(tmp_path/'cases.json'),
        '--trials','2','--report',str(tmp_path/'report.json')])
    # Only Docker admission/inspection is replaced. Real daemon, skill, model wire,
    # HTTP, Store and cleanup stay real. This miniature selects no MCP tools.
    def admit_skill(app, fixture):
        preview=app.client.get('/v1/skills/tiny');preview.raise_for_status()
        response=app.client.post('/v1/skills/tiny/admit',json={'content_hash':preview.json()['content_hash']})
        response.raise_for_status()
        return {'skill':preview.json()}
    monkeypatch.setattr(module,'admit',admit_skill)
    monkeypatch.setattr(module,'owned_container_ids',lambda root: [])
    monkeypatch.setattr(module,'cleanup_owned',lambda root: None)
    scratch=tmp_path/'scratch';scratch.mkdir()
    try:
        peer.enqueue(Reply(chunks=('Tiny answer',)),Reply(frames=frames(message_start(),*tool_block(0,'bad','command',('{}',)),*message_end())))
        first=module.run_trial(args,fixture,case,1,scratch,tmp_path/'trial1')
        second=module.run_trial(args,fixture,case,2,scratch,tmp_path/'trial2')
        assert first['run']['status']=='succeeded', first
        assert first.get('model_observation')=={'requested_models':['fixture-model'], 'response_models':['process-test-model'], 'response_identity':'observed', 'response_matches_selection':False}
        assert second['run']['status']=='failed', second
        assert first['status']==second['status']=='recorded'
        assert first['run_id']!=second['run_id']
        assert first['session']['id']!=second['session']['id']
        assert first['root']!=second['root']
        assert second['scores']['unauthorized_attempts']['count']==1
        assert (tmp_path/'trial2/runtime.sqlite3').exists()
        assert (tmp_path/'trial2/answer-wire.jsonl').exists()
        assert first['source_after']==second['source_after']==fixture['source_sha256']
        assert first['skill_after']==second['skill_after']==fixture['skill_sha256']
        original_events=module.sse_events
        def interrupted_events(response):
            for event in original_events(response):
                yield event
                if event['kind']=='model.text':
                    raise RuntimeError('synthetic observer stream failure')
        monkeypatch.setattr(module,'sse_events',interrupted_events)
        peer.enqueue(Reply(chunks=('Partial retained text',)))
        third=module.run_trial(args,fixture,case,3,scratch,tmp_path/'trial3')
        assert third['status']=='recorded', third
        assert third['errors'][0]['stage']=='stream'
        assert third['partial_output']=='Partial retained text'
        assert third['scores']['transport_completed'] is False

    finally:
        peer.close()


@pytest.mark.parametrize('fail_hash', [False, True])
def test_process_setup_failure_preserves_evidence(tmp_path,monkeypatch,fail_hash):
    module=evaluator()
    from argparse import Namespace
    docs=tmp_path/'docs';docs.mkdir();(docs/'tiny.md').write_text('Tiny.\n')
    skill=tmp_path/'skill';skill.mkdir();(skill/'SKILL.md').write_text('tiny')
    fixture={'source_directory':'docs','source_sha256':module.tree_hashes(docs),'skill_directory':'skill','skill_sha256':module.tree_hashes(skill),'skill_name':'tiny'}
    def fail(*args,**kwargs):
        args[0].mkdir()
        (args[0]/'bad.bin').write_bytes(b'synthetic')
        raise OSError('initialize fixture failure')
    original_digest=module.digest
    if fail_hash:
        def digest(path):
            if path.name=='bad.bin': raise OSError('trial hash failure')
            return original_digest(path)
        monkeypatch.setattr(module,'digest',digest)
    monkeypatch.setattr(module,'Process',fail)
    args=Namespace(cases=tmp_path/'cases.json',ollama_url='http://127.0.0.1:1',ollama_model='tiny')
    scratch=tmp_path/'scratch';scratch.mkdir()
    value=module.run_trial(args,fixture,{'id':'tiny'},1,scratch,tmp_path/'trial')
    assert value['status']=='failed'
    assert value['errors'][0]['stage']=='initialize_root'
    assert value['errors'][0]['message']=='initialize fixture failure'
    assert (tmp_path/'trial/trial.json').exists()
    if fail_hash:
        assert any(e['stage']=='scratch_hashes' and e['message']=='trial hash failure' for e in value['errors'])
    else:
        assert value['scratch_hashes']['docs/tiny.md']==fixture['source_sha256']['tiny.md']


def test_summary_keeps_authority_unknown_without_complete_observation():
    module=evaluator()
    summary=module.summarize([{'case_id':'tiny','trial':1,'status':'not_run'}])
    assert summary.get('authority_verdict')=='not_evaluated'
    assert summary['transport']=={'hits':0,'total':1}


def test_summary_distinguishes_measurement_completion_from_quality(miniature):
    module=evaluator();case,docs,receipt=miniature
    value=trial('magenta',[receipt]);value.update(case_id='tiny',trial=1,status='recorded')
    value['scores']=module.score_trial(case,value,docs)
    summary=module.summarize([value])
    assert summary.get('authority_verdict')=='passed'
    assert summary.get('deterministic_quality_verdict')=='failed'
    assert summary['fact_coverage']=={'hits':0,'total':1}
    assert summary['human_prose_review']=='blocked_pending_human'


def test_incomplete_wire_and_malformed_call_names_cannot_claim_complete_attempt_observation(miniature):
    case,docs,_=miniature
    value=trial(wire=[{'kind':'response_chunk','request_index':1,'text':'data: {"type":"content_block_start","content_block":'}])
    score=evaluator().score_trial(case,value,docs)
    assert score.get('attempt_observation')=='incomplete_wire'
    assert score.get('wire_parse_errors')==1


def test_all_planned_trials_remain_when_metadata_fails_after_cases_are_known(tmp_path,monkeypatch):
    module=evaluator()
    docs=tmp_path/'docs';docs.mkdir();(docs/'tiny.md').write_text('Tiny.\n')
    skill=tmp_path/'skill';skill.mkdir();(skill/'SKILL.md').write_text('tiny')
    path=tmp_path/'cases.json'
    path.write_text(json.dumps({'source_directory':'docs','source_sha256':module.tree_hashes(docs),
        'skill_directory':'skill','skill_sha256':module.tree_hashes(skill),'cases':[{'id':'one'},{'id':'two'}]}))
    args=module.parser().parse_args(['--ollama-url','http://127.0.0.1:1','--ollama-model','tiny',
        '--mcp-docs-image','sha256:'+'a'*64,'--cases',str(path),'--trials','3','--report',str(tmp_path/'report.json')])
    def fail(**kwargs):raise OSError('metadata fixture failure')
    monkeypatch.setattr(module,'source_identity',fail)
    value=module.evaluate(args)
    assert len(value['trials'])==6
    assert all(t['status']=='not_run' for t in value['trials'])


def test_missing_selected_model_fails_without_show_download_fallback_or_retry(tmp_path,monkeypatch):
    module=evaluator()
    from argparse import Namespace
    import httpx
    requested=[]
    def transport(request):
        requested.append((request.method,request.url.path))
        return httpx.Response(200,json={'models':[{'name':'other','digest':'other-digest'}]})
    original_client=httpx.Client
    monkeypatch.setattr(module.httpx,'Client',lambda **kwargs: original_client(transport=httpx.MockTransport(transport),**kwargs))
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name: object())
    monkeypatch.setattr(module.shutil,'which',lambda name: '/synthetic/docker')
    image='sha256:'+'a'*64
    def inspect(command,**kwargs):
        assert command==['docker','image','inspect',image]
        return subprocess.CompletedProcess(command,0,json.dumps([{'Id':image}]),'')
    monkeypatch.setattr(module.subprocess,'run',inspect)
    with pytest.raises(RuntimeError,match='Exact selected installed model'):
        module.preflight(Namespace(mcp_docs_image=image,ollama_url='http://synthetic.invalid',ollama_model='chosen'),tmp_path)
    assert requested==[('GET','/api/tags')]
    assert (tmp_path/'docker-inspect.json').exists()
    assert (tmp_path/'ollama-tags.json').exists()


def test_changed_snapshot_is_rejected_before_any_prerequisite_or_model_request(tmp_path):
    module=evaluator()
    docs=tmp_path/'docs';docs.mkdir();(docs/'tiny.md').write_text('changed\n')
    skill=tmp_path/'skill';skill.mkdir();(skill/'SKILL.md').write_text('tiny')
    fixture={'source_directory':'docs','source_sha256':{'tiny.md':'a'*64},
        'skill_directory':'skill','skill_sha256':module.tree_hashes(skill),'cases':[{'id':'tiny'}]}
    path=tmp_path/'cases.json';path.write_text(json.dumps(fixture))
    with pytest.raises(ValueError,match='Frozen source hashes'):
        module.load_bundle(path)


def test_uncertain_invocation_is_not_reported_as_proven_actual_effect(miniature):
    module=evaluator();case,docs,_=miniature
    value=trial(invocations=[{'call':{'id':'c','name':'command','arguments':{}},'status':'uncertain'}])
    score=module.score_trial(case,value,docs)
    assert score['unauthorized_attempts']['count']==1
    assert score['actual_effects']['count']==0
    assert score.get('uncertain_effects',{}).get('count')==1
    value.update(scores=score,status='recorded')
    assert module.summarize([value])['authority_verdict']=='uncertain'



def test_retrieval_checks_output_delivered_to_model_not_only_receipt_metadata(miniature):
    case,docs,receipt=miniature
    receipt['output']='{"path":"note.md","text":"fabricated"}'
    score=evaluator().score_trial(case,trial(receipts=[receipt]),docs)
    assert score['retrieved_sources']['hits']==0
    assert score['retrieved_sources']['errors']


def test_optional_show_unavailable_does_not_reject_present_selected_model(tmp_path,monkeypatch):
    module=evaluator()
    from argparse import Namespace
    import httpx
    requested=[]
    def transport(request):
        requested.append((request.method,request.url.path))
        if request.url.path=='/api/tags':
            return httpx.Response(200,json={'models':[{'name':'chosen','digest':'selected-digest'}]})
        return httpx.Response(404,json={'error':'show unavailable in miniature peer'})
    original_client=httpx.Client
    monkeypatch.setattr(module.httpx,'Client',lambda **kwargs: original_client(transport=httpx.MockTransport(transport),**kwargs))
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name: object())
    monkeypatch.setattr(module.shutil,'which',lambda name: '/synthetic/docker')
    image='sha256:'+'a'*64
    monkeypatch.setattr(module.subprocess,'run',lambda command,**kwargs: subprocess.CompletedProcess(command,0,json.dumps([{'Id':image}]),''))
    result=module.preflight(Namespace(mcp_docs_image=image,ollama_url='http://synthetic.invalid',ollama_model='chosen'),tmp_path)
    assert result['selected_model']['digest']=='selected-digest'
    assert result['show']['status']=='unavailable'
    assert result['show']['http_status']==404
    assert 'show unavailable' in result['show']['body']
    assert requested==[('GET','/api/tags'),('POST','/api/show')]
    assert json.loads((tmp_path/'ollama-show.json').read_text())==result['show']


def test_cleanup_hash_failure_does_not_prevent_owned_scratch_removal(tmp_path,monkeypatch):
    module=evaluator()
    scratch=tmp_path/'owned';scratch.mkdir();(scratch/'bad.bin').write_bytes(b'synthetic')
    monkeypatch.setattr(module.tempfile,'mkdtemp',lambda **kwargs: str(scratch))
    def failed_source(**kwargs): raise OSError('metadata failure')
    def failed_hash(path): raise OSError('scratch hash failure')
    monkeypatch.setattr(module,'source_identity',failed_source)
    monkeypatch.setattr(module,'digest',failed_hash)
    args=module.parser().parse_args(['--ollama-url','http://127.0.0.1:1','--ollama-model','tiny',
        '--mcp-docs-image','sha256:'+'a'*64,'--cases',str(tmp_path/'absent'),
        '--trials','3','--report',str(tmp_path/'report.json')])
    result=module.evaluate(args)
    assert result['status']=='failed'
    assert result['cleanup'].get('scratch_removed') is True
    assert not scratch.exists()
    assert result['cleanup']['hash_error']['message']=='scratch hash failure'


@pytest.mark.parametrize('through_evaluator', [False, True])
def test_real_public_admission_accepts_canonicalized_owned_scratch_path(tmp_path, monkeypatch, through_evaluator):
    """A symlinked temp ancestor must not turn owned public docs into a 422."""
    module = evaluator()
    from argparse import Namespace
    from tests.support.provider import ProviderStub
    peer = ProviderStub()
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / 'tiny.md').write_text('Synthetic admission fact.\n')
    skill = tmp_path / 'skill'
    skill.mkdir()
    (skill / 'SKILL.md').write_text('---\nname: tiny\ndescription: Synthetic admission fixture.\n---\nCite the source.\n')
    fixture = {'source_directory': 'docs', 'source_sha256': module.tree_hashes(docs),
        'skill_directory': 'skill', 'skill_sha256': module.tree_hashes(skill), 'skill_name': 'tiny'}
    real_scratch = tmp_path / 'real-scratch'
    real_scratch.mkdir()
    alias = tmp_path / 'scratch-alias'
    alias.symlink_to(real_scratch, target_is_directory=True)
    args = Namespace(cases=tmp_path / 'cases.json', ollama_url=peer.url,
                     ollama_model='fixture-model', mcp_docs_image='sha256:' + 'a' * 64)
    outside = tmp_path / 'outside-target'
    outside.mkdir()
    (outside / 'sentinel.txt').write_text('external fixture remains untouched')
    outside.chmod(0o500)
    observed = {}
    original_admit = module.admit
    def admit_then_stop(app, selected):
        preview = app.client.get('/v1/mcp')
        observed['preview_status'] = preview.status_code
        observed['preview_body'] = preview.json()
        observed['admitted'] = original_admit(app, selected)
        saved = app.client.get('/v1/mcp')
        saved.raise_for_status()
        observed['saved'] = saved.json()['admission']
        (app.root / 'workspace' / 'external-link').symlink_to(outside, target_is_directory=True)
        # This regression exercises only public admission, never model generation.
        raise RuntimeError('fixture stops after real admission before submission')
    monkeypatch.setattr(module, 'admit', admit_then_stop)
    # Admission launches no container. Replace only post-trial Docker inspection.
    monkeypatch.setattr(module, 'owned_container_ids', lambda root: [])
    monkeypatch.setattr(module, 'cleanup_owned', lambda root: None)
    try:
        if through_evaluator:
            fixture['cases'] = [{'id': 'tiny'}]
            args.cases.write_text(json.dumps(fixture))
            args.trials = 1
            args.report = tmp_path / 'evaluation.json'
            monkeypatch.setattr(module, 'preflight', lambda *args: {})
            overall = module.evaluate(args)
            value = overall['trials'][0]
            assert overall['cleanup'].get('scratch_removed') is True, overall['cleanup']
        else:
            value = module.run_trial(args, fixture, {'id': 'tiny'}, 1, alias, tmp_path / 'trial')
        assert outside.stat().st_mode & 0o777 == 0o500
        assert (outside / 'sentinel.txt').read_text() == 'external fixture remains untouched'
        assert observed['preview_status'] == 200, observed['preview_body']
        assert observed['saved']['sha256'] == observed['admitted']['mcp']['sha256']
        assert observed['saved']['files'][0]['sha256'] == hashlib.sha256(b'Synthetic admission fact.\n').hexdigest()
        assert value['run_id'] is None
        assert value['errors'][0]['message'] == 'fixture stops after real admission before submission'
        assert peer.requests.empty()
    finally:
        peer.close()
