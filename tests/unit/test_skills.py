import os
from pathlib import Path

import pytest

from hyperclaw.contracts import Conflict, InvalidRequest, RunRequest
from hyperclaw.config import initialize_root, load_settings
from hyperclaw.skills import Skills
from hyperclaw.store import Store


def package(root: Path, *, body='Use [rules](rules.txt).', description='Answer from documentation.'):
    directory = root / 'skills' / 'documentation-answer'
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(
        f'---\nname: documentation-answer\ndescription: "{description}"\n---\n{body}\n'
    )
    (directory / 'rules.txt').write_text('Cite the source path.')
    return directory


def test_loads_referenced_text_and_hashes_all_injected_content(tmp_path):
    directory = package(tmp_path)
    skills = Skills(tmp_path / 'skills')

    first = skills.load('documentation-answer')

    assert first.name == 'documentation-answer'
    assert first.description == 'Answer from documentation.'
    assert first.body == 'Use [rules](rules.txt).\n'
    assert [(r.path, r.text) for r in first.resources] == [
        ('rules.txt', 'Cite the source path.'),
    ]
    assert first == skills.load('documentation-answer')
    original_hash = first.content_hash
    (directory / 'rules.txt').write_text('Cite a different source path.')
    assert skills.load('documentation-answer').content_hash != original_hash
    (directory / 'rules.txt').write_text('Cite the source path.')
    (directory / 'SKILL.md').write_text(
        '---\nname: documentation-answer\ndescription: Answer from documentation.\n---\nUse [rules](rules.txt) carefully.\n'
    )
    assert skills.load('documentation-answer').content_hash != original_hash


@pytest.mark.parametrize('name', [
    '', '-bad', 'bad-', 'bad--name', 'UPPER', '../escape', 'a' * 65,
])
def test_rejects_invalid_names(tmp_path, name):
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load(name)


@pytest.mark.parametrize('front_matter', [
    'name: documentation-answer\ndescription: ok\nallowed-tools: command',
    'name: documentation-answer\nname: documentation-answer\ndescription: ok',
    'name: documentation-answer\ndescription: ok\nhooks: []',
    "name: documentation-answer\ndescription: 'single quoted'",
])
def test_rejects_unsupported_or_duplicate_yaml(tmp_path, front_matter):
    directory = package(tmp_path)
    (directory / 'SKILL.md').write_text(f'---\n{front_matter}\n---\nBody.\n')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


@pytest.mark.parametrize('target', [
    '../outside.txt', '%2e%2e/outside.txt', '/etc/passwd', r'..\outside.txt',
    'file:///etc/passwd',
])
def test_rejects_escaping_resource_links(tmp_path, target):
    directory = package(tmp_path, body=f'Read [source]({target}).')
    (tmp_path / 'skills' / 'outside.txt').write_text('outside')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


def test_external_citations_are_not_fetched_or_resources(tmp_path):
    package(tmp_path, body='See [public docs](https://example.invalid/guide.txt).')
    assert Skills(tmp_path / 'skills').load('documentation-answer').resources == ()


def test_rejects_symlinks_hardlinks_executables_nontext_and_nul(tmp_path):
    directory = package(tmp_path)
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside')
    (directory / 'rules.txt').unlink()
    (directory / 'rules.txt').symlink_to(outside)
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')

    (directory / 'rules.txt').unlink()
    os.link(outside, directory / 'rules.txt')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')

    (directory / 'rules.txt').unlink()
    (directory / 'rules.txt').write_text('safe')
    executable = directory / 'run.txt'
    executable.write_text('echo unsafe')
    executable.chmod(0o700)
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')
    executable.unlink()

    (directory / 'payload.bin').write_bytes(b'\xff\xfe')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')
    (directory / 'payload.bin').unlink()
    (directory / 'rules.txt').write_bytes(b'safe\0unsafe')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


def test_rejects_fifo_as_nonregular_package_content(tmp_path):
    directory = package(tmp_path)
    os.mkfifo(directory / 'pipe.txt')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


def test_enforces_utf8_byte_resource_document_and_combined_bounds(tmp_path):
    directory = package(tmp_path, description='é' * 513)
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')
    package_dir = tmp_path / 'skills' / 'documentation-answer'
    (package_dir / 'SKILL.md').write_text(
        '---\nname: documentation-answer\ndescription: ok\n---\n' + 'é' * 8200
    )
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')
    (package_dir / 'SKILL.md').write_text(
        '---\nname: documentation-answer\ndescription: ok\n---\n[rules](rules.txt)\n'
    )
    (package_dir / 'rules.txt').write_text('é' * 8200)
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


def test_descriptor_relative_read_is_stable_during_directory_replacement(tmp_path, monkeypatch):
    directory = package(tmp_path)
    original_listdir = os.listdir
    replaced = False

    def replace_after_open(fd):
        nonlocal replaced
        if not replaced:
            replaced = True
            directory.rename(directory.with_name('original'))
            replacement = directory
            replacement.mkdir()
            (replacement / 'SKILL.md').write_text(
                '---\nname: documentation-answer\ndescription: replacement\n---\nUnsafe.\n'
            )
        return original_listdir(fd)

    monkeypatch.setattr(os, 'listdir', replace_after_open)
    loaded = Skills(tmp_path / 'skills').load('documentation-answer')
    assert loaded.description == 'Answer from documentation.'
    assert loaded.resources[0].text == 'Cite the source path.'


def test_run_request_allows_four_distinct_valid_skill_names():
    request = RunRequest(session_id='session', generation=0, request_id='request', text='hello',
                         skills=('one', 'two', 'three', 'four'))
    assert request.skills == ('one', 'two', 'three', 'four')
    for selected in [('one', 'one'), ('one', 'two', 'three', 'four', 'five'), ('Bad',)]:
        with pytest.raises(ValueError):
            RunRequest(session_id='session', generation=0, request_id='request', text='hello',
                       skills=selected)


def test_combined_limit_counts_multiple_individually_valid_resources(tmp_path):
    directory = package(tmp_path, body='Read [one](one.txt) and [two](two.txt).')
    (directory / 'rules.txt').unlink()
    (directory / 'one.txt').write_text('a' * 16_384)
    (directory / 'two.txt').write_text('b' * 16_384)
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


def test_resource_count_accepts_sixteen_and_rejects_seventeen(tmp_path):
    links = []
    directory = package(tmp_path, body='Placeholder.')
    (directory / 'rules.txt').unlink()
    for number in range(17):
        name = f'r{number}.txt'
        (directory / name).write_text(str(number))
        links.append(f'[{number}]({name})')
    header = '---\nname: documentation-answer\ndescription: Count resources.\n---\n'
    (directory / 'SKILL.md').write_text(header + ' '.join(links[:16]) + '\n')
    assert len(Skills(tmp_path / 'skills').load('documentation-answer').resources) == 16
    (directory / 'SKILL.md').write_text(header + ' '.join(links) + '\n')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


def test_traversal_accepts_512_entries_and_rejects_513(tmp_path):
    directory = package(tmp_path, body='No local resources.')
    (directory / 'rules.txt').unlink()
    for number in range(511):
        (directory / f'{number}.txt').write_text('safe')
    Skills(tmp_path / 'skills').load('documentation-answer')
    (directory / 'overflow.txt').write_text('unsafe')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


def test_traversal_accepts_depth_sixteen_and_rejects_seventeen(tmp_path):
    directory = package(tmp_path, body='No local resources.')
    nested = directory
    for number in range(16):
        nested = nested / f'd{number}'
        nested.mkdir()
    (nested / 'leaf.txt').write_text('safe')
    Skills(tmp_path / 'skills').load('documentation-answer')
    too_deep = nested / 'd16'
    too_deep.mkdir()
    (too_deep / 'leaf.txt').write_text('unsafe')
    with pytest.raises(InvalidRequest):
        Skills(tmp_path / 'skills').load('documentation-answer')


async def test_admission_is_immutable_exact_and_survives_reopen(tmp_path):
    initialize_root(load_settings(root=tmp_path, environ={}))
    directory = package(tmp_path)
    document = Skills(tmp_path / 'skills').load('documentation-answer')
    store = await Store.open(tmp_path)
    session = await store.create_session()
    try:
        admitted = await store.admit_skill(document)
        assert admitted == document
        assert await store.skill_admissions() == [document]
        assert await store.admitted_skill(document.name, document.content_hash) == document
        run = await store.submit(
            RunRequest(session_id=session.id, generation=0, request_id='selected', text='hello',
                       skills=('documentation-answer',)),
            skill_hashes={'documentation-answer': document.content_hash},
        )
        assert run.skill_hashes == {'documentation-answer': document.content_hash}
        with pytest.raises(Conflict):
            await store.submit(run.request.model_copy(update={'request_id': 'bad'}), skill_hashes={})
    finally:
        await store.close()

    store = await Store.open(tmp_path)
    try:
        assert await store.admitted_skill(document.name, document.content_hash) == document
        await store.revoke_skill(document.name)
        assert await store.skill_admissions() == []
        with pytest.raises(Conflict):
            await store.admitted_skill(document.name, document.content_hash)
        directory.joinpath('rules.txt').write_text('changed')
        changed = Skills(tmp_path / 'skills').load('documentation-answer')
        assert changed.content_hash != document.content_hash
        with pytest.raises(Conflict):
            await store.submit(run.request.model_copy(update={'request_id': 'changed'}),
                               skill_hashes={'documentation-answer': changed.content_hash})
    finally:
        await store.close()
