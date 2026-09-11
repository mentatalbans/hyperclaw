"""Descriptor-relative loader for a deliberately narrow, inert SKILL.md subset."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlsplit

from hyperclaw.contracts import InvalidRequest, NotFound, SkillDocument, SkillResource, canonical
from hyperclaw.skill_format import (
    COMBINED_LIMIT, DESCRIPTION_LIMIT, DOCUMENT_LIMIT, NAME_LIMIT, RESOURCE_LIMIT,
    RESOURCE_LIMIT_COUNT,
)


NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
LINK = re.compile(r'!?\[[^\]\n]*\]\(([^)\n]+)\)')
ENTRY_LIMIT = 512
DEPTH_LIMIT = 16


def _invalid(message='Skill package is invalid.'):
    return InvalidRequest('invalid_skill', message)


def _valid_name(name):
    return isinstance(name, str) and len(name) <= NAME_LIMIT and NAME.fullmatch(name) is not None


def _read_regular(parent_fd, entry, expected, limit):
    fd = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        observed = os.fstat(fd)
        if ((observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino)
                or not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1
                or observed.st_mode & 0o111):
            raise _invalid()
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, min(8192, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise _invalid('Skill text exceeds its UTF-8 byte limit.')
        return b''.join(chunks)
    finally:
        os.close(fd)


def _text(raw):
    try:
        value = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise _invalid('Skill packages may contain only UTF-8 text.') from None
    if '\0' in value:
        raise _invalid('Skill text must not contain NUL.')
    return value


def _scalar(value):
    value = value.strip()
    if not value:
        raise _invalid('Skill metadata values must be nonempty single-line scalars.')
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            raise _invalid('Skill metadata supports only plain or JSON-double-quoted scalars.') from None
        if not isinstance(parsed, str) or not parsed or any(char in parsed for char in '\0\r\n'):
            raise _invalid('Skill metadata values must be nonempty strings.')
        return parsed
    if (value[0] in "'[{|>&*!?@`" or value.startswith('- ')
            or value.startswith(': ') or ' #' in value):
        raise _invalid('Skill metadata supports only plain or JSON-double-quoted scalars.')
    if '\0' in value:
        raise _invalid('Skill metadata values must not contain NUL.')
    return value


def skill_content_hash(name, description, body, resources):
    content = {'name': name, 'description': description, 'body': body,
               'resources': [resource.model_dump() for resource in resources]}
    return hashlib.sha256(canonical(content).encode('utf-8')).hexdigest()


def _metadata(document):
    lines = document.splitlines(keepends=True)
    if not lines or lines[0].rstrip('\r\n') != '---':
        raise _invalid('SKILL.md must begin with YAML front matter.')
    closing = next((i for i, line in enumerate(lines[1:], 1)
                    if line.rstrip('\r\n') == '---'), None)
    if closing is None:
        raise _invalid('SKILL.md front matter is not closed.')
    values = {}
    for line in lines[1:closing]:
        plain = line.rstrip('\r\n')
        match = re.fullmatch(r'(name|description):[ \t]+(.+)', plain)
        if match is None or match.group(1) in values:
            raise _invalid('SKILL.md front matter must contain exactly name and description.')
        values[match.group(1)] = _scalar(match.group(2))
    if set(values) != {'name', 'description'}:
        raise _invalid('SKILL.md front matter must contain exactly name and description.')
    body = ''.join(lines[closing + 1:])
    if not body.strip():
        raise _invalid('SKILL.md body must be nonempty Markdown.')
    return values['name'], values['description'], body


def _resource_paths(body):
    selected = []
    for match in LINK.finditer(body):
        destination = match.group(1).strip()
        if destination.startswith('<') and destination.endswith('>'):
            destination = destination[1:-1]
        elif any(char.isspace() for char in destination):
            raise _invalid('Local skill links must contain a single path.')
        parsed = urlsplit(destination)
        if parsed.scheme.lower() in {'http', 'https'}:
            continue
        if parsed.scheme or parsed.netloc or destination.lower().startswith('file:'):
            raise _invalid('Skill resources must be local relative paths.')
        if destination.startswith('#'):
            continue
        if '%' in destination or '\\' in destination or parsed.query:
            raise _invalid('Encoded, queried, or backslash skill paths are unsupported.')
        path = PurePosixPath(parsed.path)
        if (not parsed.path or path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts)
                or path.suffix.lower() not in {'.md', '.txt'}):
            raise _invalid('Skill resources must be relative .md or .txt paths.')
        normalized = path.as_posix()
        if normalized not in selected:
            selected.append(normalized)
        if len(selected) > RESOURCE_LIMIT_COUNT:
            raise _invalid('A skill may reference at most 16 resources.')
    return selected


class Skills:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def names(self):
        try:
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return []
        except OSError:
            raise _invalid() from None
        try:
            return sorted(name for name in os.listdir(fd) if _valid_name(name))
        finally:
            os.close(fd)

    def load(self, name: str) -> SkillDocument:
        if not _valid_name(name):
            raise _invalid('Invalid skill name.')
        try:
            root_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                package_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=root_fd)
            finally:
                os.close(root_fd)
        except FileNotFoundError:
            raise NotFound('skill_not_found', 'The requested skill does not exist.') from None
        except OSError:
            raise _invalid() from None
        try:
            files = {}
            entries = 0

            def walk(directory_fd, prefix='', depth=0):
                nonlocal entries
                for entry in sorted(os.listdir(directory_fd)):
                    entries += 1
                    if entries > ENTRY_LIMIT:
                        raise _invalid('Skill package exceeds 512 entries.')
                    try:
                        info = os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
                    except OSError:
                        raise _invalid() from None
                    path = f'{prefix}/{entry}' if prefix else entry
                    if stat.S_ISLNK(info.st_mode):
                        raise _invalid('Skill packages must not contain symbolic links.')
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= DEPTH_LIMIT:
                            raise _invalid('Skill package exceeds depth 16.')
                        try:
                            child = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                            dir_fd=directory_fd)
                        except OSError:
                            raise _invalid() from None
                        try:
                            walk(child, path, depth + 1)
                        finally:
                            os.close(child)
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise _invalid('Skill packages may contain only regular text files.')
                    if Path(entry).suffix.lower() not in {'.md', '.txt'}:
                        raise _invalid('Skill packages may contain only Markdown and text files.')
                    limit = DOCUMENT_LIMIT if path == 'SKILL.md' else RESOURCE_LIMIT
                    try:
                        raw = _read_regular(directory_fd, entry, info, limit)
                    except OSError:
                        raise _invalid() from None
                    files[path] = (raw, _text(raw))

            walk(package_fd)
        finally:
            os.close(package_fd)
        if 'SKILL.md' not in files:
            raise _invalid('Skill package has no SKILL.md.')
        document_raw, document_text = files['SKILL.md']
        loaded_name, description, body = _metadata(document_text)
        if loaded_name != name or not _valid_name(loaded_name):
            raise _invalid('SKILL.md name does not match its package directory.')
        if len(description.encode('utf-8')) > DESCRIPTION_LIMIT:
            raise _invalid('Skill description exceeds 1,024 UTF-8 bytes.')
        resources = []
        combined = len(document_raw)
        for path in _resource_paths(body):
            if path == 'SKILL.md' or path not in files:
                raise _invalid('A referenced skill resource is missing or invalid.')
            raw, value = files[path]
            combined += len(raw)
            if combined > COMBINED_LIMIT:
                raise _invalid('Skill document and resources exceed 32,768 UTF-8 bytes.')
            resources.append(SkillResource(path=path, text=value,
                                           sha256=hashlib.sha256(raw).hexdigest()))
        return SkillDocument(name=loaded_name, description=description, body=body,
                             content_hash=skill_content_hash(loaded_name, description, body, resources),
                             resources=tuple(resources))
