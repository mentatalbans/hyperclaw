"""Explicit combined Qwen + admitted container documentation acceptance."""
import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from tests.support.process import Process, events, submit, workspace_grants
from tests.live.test_recovery_docker import cleanup_owned, owned_container_ids

pytestmark = [pytest.mark.ollama, pytest.mark.docker]
ROOT = Path(__file__).resolve().parents[2]


def test_qwen_answers_documentation_question_with_unseen_source_marker(tmp_path, request):
    image = request.config.getoption('--mcp-docs-image')
    assert image, 'Build examples/mcp-docs/Dockerfile explicitly; pass its immutable ID as --mcp-docs-image.'
    docs = tmp_path / 'public-docs'
    docs.mkdir()
    marker = 'opaque-doc-verification-' + uuid4().hex
    text = (ROOT/'README.md').read_text() + '\n\nDocumentation verification marker: ' + marker + '\n'
    (docs/'README.md').write_text(text)
    app = Process(tmp_path/'runtime', request.config.getoption('--ollama-url'),
                  model=request.config.getoption('--ollama-model'), timeout=180)
    config = app.root/'config.toml'
    config.write_text(config.read_text().replace('mcp_docs_path = ""', 'mcp_docs_path = ' + json.dumps(str(docs))).replace('mcp_docs_image = ""', 'mcp_docs_image = ' + json.dumps(image)))
    skill_dir = app.root/'skills'/'documentation-answer'
    shutil.copytree(ROOT/'examples/skills/documentation-answer', skill_dir)
    try:
        app.start()
        skill = app.client.get('/v1/skills/documentation-answer').json()
        app.client.post('/v1/skills/documentation-answer/admit', json={'content_hash': skill['content_hash']}).raise_for_status()
        preview = app.client.get('/v1/mcp').json()
        app.client.post('/v1/mcp/admit', json={'expected_sha256': preview['sha256']}).raise_for_status()
        before = workspace_grants(app)
        assert before == []
        run = submit(app, 'Use the admitted public README.md to answer: what is the default runtime root and how does the runtime preserve tool outcomes across restart? Cite the source path and line numbers. Also find and quote the documentation verification marker from that file. Use search limits no greater than 5 and read at most 80 lines per call.',
                     tools=['mcp_docs_search', 'mcp_docs_read'], skills=['documentation-answer'])
        observed = events(app, run['id'])
        saved = app.client.get(f"/v1/runs/{run['id']}").json()
        assert saved['status'] == 'succeeded', saved
        assert saved['skill_hashes'] == {'documentation-answer': skill['content_hash']}
        assert marker in saved['output'] and 'README.md' in saved['output']
        assert '.hyperclaw-v2' in saved['output']
        receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
        assert receipts and any(marker in r['output'] for r in receipts)
        assert all(r['status'] == 'succeeded' and r['evidence']['terminated'] for r in receipts)
        assert any(source['sha256'] == hashlib.sha256(text.encode()).hexdigest()
                   for receipt in receipts for source in receipt['evidence']['sources'])
        assert all(r['evidence']['content_hash'] == preview['content_hash'] for r in receipts)
        assert workspace_grants(app) == before
        assert owned_container_ids(app.root) == []
        evidence = ROOT/'test-results/m5-task2/live-qwen.json'
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps({'run': saved, 'receipts': receipts, 'manifest': preview}, indent=2))
    finally:
        app.stop(); cleanup_owned(app.root)
