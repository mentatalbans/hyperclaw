import hashlib
import os

import pytest

from hyperclaw.contracts import InvalidRequest
from hyperclaw.execution.workspace import Workspace, WorkspaceUncertain


HELLO_SHA256 = '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'


def test_workspace_reads_lists_searches_and_verifies_writes(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'notes').mkdir()
    workspace = Workspace(root)

    artifact = workspace.write('notes/answer.txt', 'hello')

    assert artifact == {
        'path': 'notes/answer.txt',
        'sha256': HELLO_SHA256,
        'size_bytes': 5,
        'verified': True,
    }
    assert workspace.read('notes/answer.txt') == 'hello'
    assert workspace.list('.') == ['notes']
    assert workspace.list('notes') == ['answer.txt']
    assert workspace.search('ell') == [
        {'path': 'notes/answer.txt', 'line': 1, 'text': 'hello'},
    ]
    assert workspace.inspect('notes/answer.txt') == artifact
    assert workspace.inspect('notes/answer.txt', expected_sha256='0' * 64) == {
        **artifact,
        'verified': False,
    }
    assert str(root.resolve()) in workspace.identity

    workspace.close()
    workspace.close()
    with pytest.raises(InvalidRequest):
        workspace.read('notes/answer.txt')


def test_write_reports_wrong_expected_hash_without_changing_observed_artifact(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    workspace = Workspace(root)

    artifact = workspace.write('answer.txt', 'hello', expected_sha256='0' * 64)

    assert artifact == {
        'path': 'answer.txt',
        'sha256': HELLO_SHA256,
        'size_bytes': 5,
        'verified': False,
    }
    assert workspace.read('answer.txt') == 'hello'


def test_write_raises_uncertain_when_published_file_cannot_be_inspected(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('sentinel')
    workspace = Workspace(root)

    def fail_readback(*_args):
        raise OSError('injected readback failure')

    monkeypatch.setattr(workspace, '_read_leaf', fail_readback)
    with pytest.raises(WorkspaceUncertain) as caught:
        workspace.write('answer.txt', 'hello')

    assert caught.value.code == 'workspace_uncertain'
    assert (root / 'answer.txt').read_text() == 'hello'
    assert outside.read_text() == 'sentinel'


@pytest.mark.parametrize('unsafe', [
    '../outside.txt',
    'nested/../../outside.txt',
    '/etc/passwd',
    '',
])
def test_workspace_rejects_paths_outside_its_root(tmp_path, unsafe):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('sentinel')
    workspace = Workspace(root)

    with pytest.raises(InvalidRequest):
        workspace.read(unsafe)
    with pytest.raises(InvalidRequest):
        workspace.write(unsafe, 'changed')

    assert outside.read_text() == 'sentinel'


def test_workspace_rejects_symlinks_and_preserves_outside_sentinel(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()
    sentinel = outside_dir / 'sentinel.txt'
    sentinel.write_text('keep')
    (root / 'link').symlink_to(outside_dir, target_is_directory=True)
    (root / 'leaf').symlink_to(sentinel)
    workspace = Workspace(root)

    for unsafe in ('link/sentinel.txt', 'leaf'):
        with pytest.raises(InvalidRequest):
            workspace.read(unsafe)
        with pytest.raises(InvalidRequest):
            workspace.write(unsafe, 'changed')

    assert sentinel.read_text() == 'keep'
    assert (root / 'leaf').is_symlink()


def test_workspace_rejects_fifos_devices_and_hardlinks(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('sentinel')
    os.mkfifo(root / 'pipe')
    os.link(outside, root / 'hardlink')
    workspace = Workspace(root)

    for unsafe in ('pipe', 'hardlink'):
        with pytest.raises(InvalidRequest):
            workspace.read(unsafe)
        with pytest.raises(InvalidRequest):
            workspace.inspect(unsafe)
        with pytest.raises(InvalidRequest):
            workspace.write(unsafe, 'changed')

    device_workspace = Workspace('/dev')
    try:
        with pytest.raises(InvalidRequest):
            device_workspace.read('null')
        with pytest.raises(InvalidRequest):
            device_workspace.inspect('null')
    finally:
        device_workspace.close()
    assert outside.read_text() == 'sentinel'


def test_workspace_requires_existing_real_parent_and_live_root_identity(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    workspace = Workspace(root)

    with pytest.raises(InvalidRequest):
        workspace.write('missing/answer.txt', 'hello')

    moved = tmp_path / 'moved-workspace'
    root.rename(moved)
    root.mkdir()
    with pytest.raises(InvalidRequest):
        workspace.write('answer.txt', 'changed')
    assert not (root / 'answer.txt').exists()
    assert not (moved / 'answer.txt').exists()


def test_workspace_bounds_file_content_and_validates_hashes(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    oversized = root / 'oversized.txt'
    oversized.write_bytes(b'x' * (65536 + 1))
    workspace = Workspace(root)

    with pytest.raises(InvalidRequest):
        workspace.read('oversized.txt')
    with pytest.raises(InvalidRequest):
        workspace.inspect('oversized.txt')
    with pytest.raises(InvalidRequest):
        workspace.write('new.txt', 'x' * (65536 + 1))
    with pytest.raises(InvalidRequest):
        workspace.read('oversized.txt', limit=65537)
    with pytest.raises(InvalidRequest):
        workspace.inspect('oversized.txt', expected_sha256='not-a-hash')


def test_search_is_recursive_deterministic_and_does_not_follow_links(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'a').mkdir()
    (root / 'b').mkdir()
    (root / 'a' / 'two.txt').write_text('skip\nneedle two\n')
    (root / 'b' / 'one.txt').write_text('needle one\n')
    outside = tmp_path / 'secret.txt'
    outside.write_text('needle secret')
    (root / 'a' / 'secret-link').symlink_to(outside)
    workspace = Workspace(root)

    assert workspace.search('needle') == [
        {'path': 'a/two.txt', 'line': 2, 'text': 'needle two'},
        {'path': 'b/one.txt', 'line': 1, 'text': 'needle one'},
    ]
    with pytest.raises(InvalidRequest):
        workspace.search('needle', 'a/secret-link')


def test_workspace_rejects_a_symlink_root(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    link = tmp_path / 'workspace-link'
    link.symlink_to(root, target_is_directory=True)

    with pytest.raises(InvalidRequest):
        Workspace(link)


def test_inspect_hashes_the_file_bytes(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    raw = 'snowman: ☃'.encode()
    (root / 'unicode.txt').write_bytes(raw)
    workspace = Workspace(root)

    assert workspace.inspect('unicode.txt') == {
        'path': 'unicode.txt',
        'sha256': hashlib.sha256(raw).hexdigest(),
        'size_bytes': len(raw),
        'verified': True,
    }
