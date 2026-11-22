"""Publish complete secret bytes once using native filelock and atomic replace."""
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

_LOCK_TIMEOUT = 10


def load_or_create_secret(path: Path, factory: Callable[[], bytes]) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=_LOCK_TIMEOUT):
        try:
            return path.read_bytes()
        except FileNotFoundError:
            pass
        value = factory()
        temporary = None
        try:
            # tempfile creates mode 0600 on Unix, on the same filesystem as target.
            with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                    prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            return value
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
