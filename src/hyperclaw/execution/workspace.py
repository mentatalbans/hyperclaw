"""Descriptor-scoped, bounded file operations for one selected workspace."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import stat
import threading
from typing import Iterator

from hyperclaw.contracts import InvalidRequest, RuntimeErrorBase


MAX_FILE_BYTES = 65_536
MAX_LIST_ENTRIES = 1_024
MAX_LIST_BYTES = 65_536
MAX_SEARCH_DEPTH = 16
MAX_SEARCH_ENTRIES = 1_024
MAX_SEARCH_FILES = 256
MAX_SEARCH_RESULTS = 100
MAX_SEARCH_SCAN_BYTES = 1_048_576
MAX_SEARCH_OUTPUT_BYTES = 65_536
MAX_PATH_BYTES = 4_096
MAX_PATH_PARTS = 64
MAX_QUERY_BYTES = 4_096

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


def _invalid(message: str) -> InvalidRequest:
    return InvalidRequest('invalid_workspace_path', message)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _regular_single_link(value: os.stat_result) -> bool:
    return stat.S_ISREG(value.st_mode) and value.st_nlink == 1


class WorkspaceUncertain(RuntimeErrorBase):
    """The atomic publication completed, but its final bytes were not observed."""

    code = 'workspace_uncertain'
    message = 'Workspace artifact publication could not be verified.'


class Workspace:
    """Own a stable directory descriptor and expose safe relative file tools."""

    def __init__(self, path: str | os.PathLike[str]):
        self._lock = threading.RLock()
        self._root_fd: int | None = None
        try:
            raw_path = os.fspath(path)
            if not isinstance(raw_path, str) or not raw_path or '\x00' in raw_path:
                raise _invalid('Workspace must be a real directory.')
            original = os.lstat(raw_path)
            if stat.S_ISLNK(original.st_mode) or not stat.S_ISDIR(original.st_mode):
                raise _invalid('Workspace must be a real directory, not a symlink.')
            canonical = os.path.realpath(os.path.abspath(raw_path))
            root_fd = os.open(raw_path, _DIRECTORY_FLAGS)
            opened = os.fstat(root_fd)
            current = os.stat(canonical, follow_symlinks=False)
            if not stat.S_ISDIR(opened.st_mode) or not _same_file(original, opened) or not _same_file(opened, current):
                os.close(root_fd)
                raise _invalid('Workspace identity changed while it was opened.')
        except InvalidRequest:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise _invalid('Workspace directory is unavailable or unsafe.') from exc

        self.path = Path(canonical)
        self._root_fd = root_fd
        self._root_device = opened.st_dev
        self._root_inode = opened.st_ino
        self.identity = f'{canonical}:{opened.st_dev}:{opened.st_ino}'

    def close(self) -> None:
        with self._lock:
            if self._root_fd is not None:
                os.close(self._root_fd)
                self._root_fd = None

    def read(self, path: str, limit: int = MAX_FILE_BYTES) -> str:
        with self._lock:
            try:
                if not isinstance(limit, int) or isinstance(limit, bool) or not 0 < limit <= MAX_FILE_BYTES:
                    raise _invalid('Read limit must be between 1 and 65536 bytes.')
                normalized, parts = self._relative_path(path)
                with self._parent(parts) as (parent_fd, leaf, parent_parts):
                    raw, _ = self._read_leaf(parent_fd, leaf, limit)
                    self._verify_directory(parent_parts, parent_fd)
                self._check_root()
                return raw.decode('utf-8')
            except InvalidRequest:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise _invalid('Workspace file cannot be read safely.') from exc

    def list(self, path: str = '.') -> list[str]:
        with self._lock:
            try:
                _, parts = self._relative_path(path, allow_dot=True)
                with self._directory(parts) as directory_fd:
                    names = sorted(os.listdir(directory_fd))
                    if len(names) > MAX_LIST_ENTRIES:
                        raise _invalid('Workspace directory contains too many entries.')
                    if sum(len(name.encode('utf-8')) + 1 for name in names) > MAX_LIST_BYTES:
                        raise _invalid('Workspace directory listing exceeds 64 KiB.')
                    self._verify_directory(parts, directory_fd)
                self._check_root()
                return names
            except InvalidRequest:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise _invalid('Workspace directory cannot be listed safely.') from exc

    def search(self, query: str, path: str = '.') -> list[dict]:
        with self._lock:
            try:
                if not isinstance(query, str) or not query or len(query.encode('utf-8')) > MAX_QUERY_BYTES:
                    raise _invalid('Search query must contain at most 4096 UTF-8 bytes.')
                normalized, parts = self._relative_path(path, allow_dot=True)
                results: list[dict] = []
                state = {'entries': 0, 'files': 0, 'bytes': 0, 'output': 0}
                if normalized == '.':
                    with self._directory(parts) as directory_fd:
                        self._search_directory(directory_fd, (), query, results, state, depth=0)
                        self._verify_directory(parts, directory_fd)
                else:
                    with self._parent(parts) as (parent_fd, leaf, parent_parts):
                        target = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                        if stat.S_ISDIR(target.st_mode):
                            with self._child_directory(parent_fd, leaf, target) as directory_fd:
                                self._search_directory(directory_fd, tuple(parts), query, results, state, depth=0)
                        elif _regular_single_link(target):
                            self._search_file(parent_fd, leaf, normalized, query, results, state)
                        else:
                            raise _invalid('Search path must be a real directory or single-link regular file.')
                        self._verify_directory(parent_parts, parent_fd)
                self._check_root()
                return results
            except InvalidRequest:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise _invalid('Workspace cannot be searched safely.') from exc

    def write(self, path: str, content: str, expected_sha256: str | None = None) -> dict:
        with self._lock:
            try:
                normalized, parts = self._relative_path(path)
                if not isinstance(content, str):
                    raise _invalid('Workspace content must be text.')
                raw = content.encode('utf-8')
                if len(raw) > MAX_FILE_BYTES:
                    raise _invalid('Workspace writes cannot exceed 64 KiB.')
                self._validate_expected_hash(expected_sha256)
                with self._parent(parts) as (parent_fd, leaf, parent_parts):
                    original = self._target_for_write(parent_fd, leaf)
                    self._verify_directory(parent_parts, parent_fd)
                    temporary = f'.hyperclaw-{secrets.token_hex(16)}.tmp'
                    temporary_fd: int | None = None
                    temporary_exists = False
                    published = False
                    try:
                        temporary_fd = os.open(
                            temporary,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=parent_fd,
                        )
                        temporary_exists = True
                        written = 0
                        while written < len(raw):
                            written += os.write(temporary_fd, raw[written:])
                        os.fsync(temporary_fd)
                        temporary_stat = os.fstat(temporary_fd)
                        if not _regular_single_link(temporary_stat):
                            raise _invalid('Temporary workspace artifact is unsafe.')
                        os.close(temporary_fd)
                        temporary_fd = None

                        self._check_root()
                        self._verify_directory(parent_parts, parent_fd)
                        self._target_unchanged(parent_fd, leaf, original)
                        os.replace(temporary, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                        temporary_exists = False
                        published = True
                        os.fsync(parent_fd)

                        observed, observed_stat = self._read_leaf(parent_fd, leaf, MAX_FILE_BYTES)
                        if not _same_file(temporary_stat, observed_stat):
                            raise _invalid('Workspace artifact changed during publication.')
                        self._verify_directory(parent_parts, parent_fd)
                        self._check_root()
                    except Exception as exc:
                        if published:
                            raise WorkspaceUncertain() from exc
                        raise
                    finally:
                        if temporary_fd is not None:
                            os.close(temporary_fd)
                        if temporary_exists:
                            try:
                                os.unlink(temporary, dir_fd=parent_fd)
                            except FileNotFoundError:
                                pass
                return self._artifact(normalized, observed, expected_sha256)
            except InvalidRequest:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise _invalid('Workspace file cannot be written safely.') from exc

    def inspect(self, path: str, expected_sha256: str | None = None) -> dict:
        with self._lock:
            try:
                normalized, parts = self._relative_path(path)
                self._validate_expected_hash(expected_sha256)
                with self._parent(parts) as (parent_fd, leaf, parent_parts):
                    raw, _ = self._read_leaf(parent_fd, leaf, MAX_FILE_BYTES)
                    self._verify_directory(parent_parts, parent_fd)
                self._check_root()
                return self._artifact(normalized, raw, expected_sha256)
            except InvalidRequest:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise _invalid('Workspace file cannot be inspected safely.') from exc

    def _relative_path(self, path: str, *, allow_dot: bool = False) -> tuple[str, tuple[str, ...]]:
        if not isinstance(path, str) or not path or '\x00' in path or len(path.encode('utf-8')) > MAX_PATH_BYTES:
            raise _invalid('Workspace path is invalid.')
        if os.path.isabs(path):
            raise _invalid('Workspace paths must be relative.')
        if path == '.' and allow_dot:
            return '.', ()
        parts = tuple(path.split('/'))
        if len(parts) > MAX_PATH_PARTS or any(part in ('', '.', '..') for part in parts):
            raise _invalid('Workspace path cannot contain empty, current, or parent components.')
        return '/'.join(parts), parts

    def _validate_expected_hash(self, expected: str | None) -> None:
        if expected is not None and (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(character not in '0123456789abcdef' for character in expected)
        ):
            raise _invalid('Expected SHA-256 must be 64 lowercase hexadecimal characters.')

    def _check_root(self) -> int:
        if self._root_fd is None:
            raise _invalid('Workspace is closed.')
        opened = os.fstat(self._root_fd)
        current = os.stat(self.path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_dev != self._root_device
            or opened.st_ino != self._root_inode
            or not _same_file(opened, current)
            or not stat.S_ISDIR(current.st_mode)
        ):
            raise _invalid('Workspace identity no longer matches the selected directory.')
        return self._root_fd

    def _open_directory_components(self, parts: tuple[str, ...]) -> int:
        current_fd = os.dup(self._check_root())
        try:
            for part in parts:
                next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
                opened = os.fstat(next_fd)
                if not stat.S_ISDIR(opened.st_mode):
                    os.close(next_fd)
                    raise _invalid('Workspace path component is not a real directory.')
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except BaseException:
            os.close(current_fd)
            raise

    @contextmanager
    def _directory(self, parts: tuple[str, ...]) -> Iterator[int]:
        directory_fd = self._open_directory_components(parts)
        try:
            yield directory_fd
        finally:
            os.close(directory_fd)

    @contextmanager
    def _parent(self, parts: tuple[str, ...]) -> Iterator[tuple[int, str, tuple[str, ...]]]:
        if not parts:
            raise _invalid('A file path is required.')
        parent_parts = parts[:-1]
        with self._directory(parent_parts) as parent_fd:
            yield parent_fd, parts[-1], parent_parts

    @contextmanager
    def _child_directory(
        self, parent_fd: int, name: str, expected: os.stat_result
    ) -> Iterator[int]:
        child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        try:
            opened = os.fstat(child_fd)
            if not stat.S_ISDIR(opened.st_mode) or not _same_file(expected, opened):
                raise _invalid('Workspace directory changed during traversal.')
            yield child_fd
        finally:
            os.close(child_fd)

    def _verify_directory(self, parts: tuple[str, ...], held_fd: int) -> None:
        with self._directory(parts) as current_fd:
            if not _same_file(os.fstat(held_fd), os.fstat(current_fd)):
                raise _invalid('Workspace directory changed during traversal.')

    def _read_leaf(self, parent_fd: int, leaf: str, limit: int) -> tuple[bytes, os.stat_result]:
        expected = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        if not _regular_single_link(expected):
            raise _invalid('Workspace files must be single-link regular files.')
        if expected.st_size > limit:
            raise _invalid('Workspace file exceeds the selected read limit.')
        file_fd = os.open(leaf, _READ_FLAGS, dir_fd=parent_fd)
        try:
            before = os.fstat(file_fd)
            if not _regular_single_link(before) or not _same_file(expected, before):
                raise _invalid('Workspace file changed during traversal.')
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(file_fd, min(remaining, 65_536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b''.join(chunks)
            after = os.fstat(file_fd)
            if (
                not _same_file(before, after)
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or len(raw) > limit
            ):
                raise _invalid('Workspace file changed or exceeded its limit while being read.')
            return raw, after
        finally:
            os.close(file_fd)

    def _target_for_write(self, parent_fd: int, leaf: str) -> os.stat_result | None:
        try:
            target = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not _regular_single_link(target):
            raise _invalid('Workspace write target must be a single-link regular file.')
        return target

    def _target_unchanged(self, parent_fd: int, leaf: str, original: os.stat_result | None) -> None:
        try:
            current = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if original is None:
                return
            raise _invalid('Workspace write target disappeared before publication.') from None
        if original is None or not _regular_single_link(current) or not _same_file(original, current):
            raise _invalid('Workspace write target changed before publication.')

    def _artifact(self, path: str, raw: bytes, expected_sha256: str | None) -> dict:
        digest = hashlib.sha256(raw).hexdigest()
        return {
            'path': path,
            'sha256': digest,
            'size_bytes': len(raw),
            'verified': expected_sha256 is None or digest == expected_sha256,
        }

    def _search_directory(
        self,
        directory_fd: int,
        relative_parts: tuple[str, ...],
        query: str,
        results: list[dict],
        state: dict[str, int],
        *,
        depth: int,
    ) -> None:
        if depth > MAX_SEARCH_DEPTH:
            raise _invalid('Workspace search exceeds its maximum depth.')
        for name in sorted(os.listdir(directory_fd)):
            if len(results) >= MAX_SEARCH_RESULTS:
                return
            state['entries'] += 1
            if state['entries'] > MAX_SEARCH_ENTRIES:
                raise _invalid('Workspace search contains too many entries.')
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            relative = '/'.join((*relative_parts, name))
            if stat.S_ISDIR(entry.st_mode):
                with self._child_directory(directory_fd, name, entry) as child_fd:
                    self._search_directory(child_fd, (*relative_parts, name), query, results, state, depth=depth + 1)
            elif _regular_single_link(entry):
                self._search_file(directory_fd, name, relative, query, results, state)

    def _search_file(
        self,
        parent_fd: int,
        leaf: str,
        path: str,
        query: str,
        results: list[dict],
        state: dict[str, int],
    ) -> None:
        state['files'] += 1
        if state['files'] > MAX_SEARCH_FILES:
            raise _invalid('Workspace search contains too many files.')
        raw, _ = self._read_leaf(parent_fd, leaf, MAX_FILE_BYTES)
        state['bytes'] += len(raw)
        if state['bytes'] > MAX_SEARCH_SCAN_BYTES:
            raise _invalid('Workspace search exceeds its scan limit.')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            return
        for line_number, line in enumerate(text.splitlines(), start=1):
            if query not in line:
                continue
            result = {'path': path, 'line': line_number, 'text': line}
            size = len(path.encode('utf-8')) + len(line.encode('utf-8')) + 32
            if len(results) >= MAX_SEARCH_RESULTS or state['output'] + size > MAX_SEARCH_OUTPUT_BYTES:
                return
            results.append(result)
            state['output'] += size
