"""Immutable local objects with descriptor-relative, symlink-safe access."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import re
import stat
from typing import Iterator
from urllib.parse import unquote, urlsplit
from uuid import uuid4


class LocalArtifactStore:
    def __init__(self, base_dir: Path):
        # A configured root may itself be a development symlink. Pin its target;
        # object paths beneath that trusted root must never follow symlinks.
        self.base_dir = base_dir.resolve()

    def put_bytes(self, *, filename: str, data: bytes) -> str:
        # filename is a compatibility/display hint, never an object path.
        root = self._open_root(create=True)
        try:
            while True:
                key = uuid4().hex
                try:
                    descriptor = os.open(
                        key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=root,
                    )
                    break
                except FileExistsError:
                    # Even a random-key collision must never overwrite bytes.
                    continue
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
            except BaseException:
                os.unlink(key, dir_fd=root)
                raise
        finally:
            os.close(root)
        return (self.base_dir / key).as_uri()

    def _open_root(self, *, create: bool = False) -> int:
        # Walk the canonical root itself without following replacement symlinks
        # in its ancestors. Creation is also descriptor-relative.
        directory = os.open(self.base_dir.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.base_dir.parts[1:]:
                if create:
                    try:
                        os.mkdir(component, dir_fd=directory)
                    except FileExistsError:
                        pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            return directory
        except BaseException:
            os.close(directory)
            raise

    def exists(self, storage_uri: str) -> bool:
        try:
            with self._open_for_read(storage_uri):
                return True
        except FileNotFoundError:
            return False

    def iter_bytes(self, storage_uri: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("Artifact chunk size must be positive")
        with self._open_for_read(storage_uri) as source:
            while chunk := source.read(chunk_size):
                yield chunk

    def _relative_parts(self, storage_uri: str) -> tuple[str, ...]:
        try:
            if not isinstance(storage_uri, str) or any(ord(c) < 32 for c in storage_uri):
                raise ValueError
            parsed = urlsplit(storage_uri)
            if (parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment
                or re.search(r"%(?![0-9a-fA-F]{2})", parsed.path)):
                raise ValueError
            decoded = unquote(parsed.path, errors="strict")
            path = Path(decoded)
            if (not path.is_absolute() or ".." in path.parts
                or any(ord(c) < 32 for c in decoded)):
                raise ValueError
            parts = path.relative_to(self.base_dir).parts
            if not parts:
                raise ValueError
            return parts
        except (ValueError, UnicodeError, TypeError):
            # Never echo private physical locators through content API errors.
            raise ValueError("Invalid local artifact locator") from None

    @contextmanager
    def _open_for_read(self, storage_uri: str):
        parts = self._relative_parts(storage_uri)
        directory = None
        descriptor = None
        try:
            directory = self._open_root()
            # dir_fd + O_NOFOLLOW prevents component replacement from redirecting
            # reads outside the root, including between validation and opening.
            for component in parts[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Invalid local artifact locator")
            source = os.fdopen(descriptor, "rb")
            descriptor = None  # source now owns the descriptor
            with source:
                yield source
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError("Invalid local artifact locator") from None
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
