"""
Type-level tests for truenas_os_pyutils.io.

Each test uses assert_type() to pin the static type mypy must infer for
yielded values. The tests are also valid pytest tests that execute at runtime.
"""
from pathlib import Path
from typing import IO, Any, BinaryIO, TextIO, assert_type

from truenas_os_pyutils.io import SymlinkInPathError, atomic_write, safe_open


def test_safe_open_yields_io(tmp_path: Path) -> None:
    f_path = str(tmp_path / 'test.txt')
    (tmp_path / 'test.txt').write_text('hello')
    with safe_open(f_path) as f:
        assert_type(f, IO[Any])


def test_atomic_write_default_mode_yields_text_io(tmp_path: Path) -> None:
    with atomic_write(str(tmp_path / 'out.txt')) as f:
        assert_type(f, TextIO)
        f.write('hello')


def test_atomic_write_text_mode_yields_text_io(tmp_path: Path) -> None:
    with atomic_write(str(tmp_path / 'out.txt'), 'w') as f:
        assert_type(f, TextIO)
        f.write('hello')


def test_atomic_write_binary_mode_yields_binary_io(tmp_path: Path) -> None:
    with atomic_write(str(tmp_path / 'out.bin'), 'wb') as f:
        assert_type(f, BinaryIO)
        f.write(b'hello')


def test_symlink_in_path_error_is_oserror() -> None:
    e: OSError = SymlinkInPathError('/x')
    assert_type(e, OSError)
