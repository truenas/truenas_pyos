"""
Type-level tests for truenas_os_pyutils.truenas_shutil.

Each test uses assert_type() to pin the static type mypy must infer.  The
tests are also valid pytest tests that execute at runtime.
"""
from pathlib import Path
from typing import assert_type

from truenas_os_pyutils.truenas_shutil import (
    CopyFlags,
    CopyTreeConfig,
    CopyTreeOp,
    CopyTreeStats,
    ReportingCallback,
    clonefile,
    copyfile,
    copysendfile,
    copytree,
    copyuserspace,
)


def test_copytree_returns_stats(tmp_path: Path) -> None:
    src = str(tmp_path / "src")
    Path(src).mkdir()
    dst = str(tmp_path / "dst")
    stats = copytree(src, dst, CopyTreeConfig())
    assert_type(stats, CopyTreeStats)


def test_clonefile_returns_int(tmp_path: Path) -> None:
    import os as _os

    src = tmp_path / "a"
    dst = tmp_path / "b"
    src.write_bytes(b"x")
    dst.write_bytes(b"")
    src_fd = _os.open(str(src), _os.O_RDONLY)
    dst_fd = _os.open(str(dst), _os.O_RDWR)
    try:
        n = clonefile(src_fd, dst_fd)
        assert_type(n, int)
    finally:
        _os.close(src_fd)
        _os.close(dst_fd)


def test_copy_helpers_return_int(tmp_path: Path) -> None:
    import os as _os

    src = tmp_path / "a"
    dst = tmp_path / "b"
    src.write_bytes(b"x")
    dst.write_bytes(b"")
    src_fd = _os.open(str(src), _os.O_RDONLY)
    dst_fd = _os.open(str(dst), _os.O_RDWR)
    try:
        assert_type(copyfile(src_fd, dst_fd), int)
    finally:
        _os.close(src_fd)
        _os.close(dst_fd)
    src_fd = _os.open(str(src), _os.O_RDONLY)
    dst2 = tmp_path / "c"
    dst2.write_bytes(b"")
    dst2_fd = _os.open(str(dst2), _os.O_RDWR)
    try:
        assert_type(copysendfile(src_fd, dst2_fd), int)
    finally:
        _os.close(src_fd)
        _os.close(dst2_fd)
    src_fd = _os.open(str(src), _os.O_RDONLY)
    dst3 = tmp_path / "d"
    dst3.write_bytes(b"")
    dst3_fd = _os.open(str(dst3), _os.O_RDWR)
    try:
        assert_type(copyuserspace(src_fd, dst3_fd), int)
    finally:
        _os.close(src_fd)
        _os.close(dst3_fd)


def test_copyflags_or_returns_copyflags() -> None:
    flags = CopyFlags.XATTRS | CopyFlags.OWNER
    assert_type(flags, CopyFlags)


def test_copytreeop_member_is_copytreeop() -> None:
    op = CopyTreeOp.DEFAULT
    assert_type(op, CopyTreeOp)


def test_copytree_config_reporting_callback_field_typing() -> None:
    def cb(dir_stack, state, private):  # type: ignore[no-untyped-def]
        pass

    cfg = CopyTreeConfig(reporting_callback=cb)
    assert_type(cfg.reporting_callback, ReportingCallback | None)


def test_copytree_config_reporting_callback_accepts_none() -> None:
    cfg = CopyTreeConfig(reporting_callback=None)
    assert_type(cfg.reporting_callback, ReportingCallback | None)


def test_copytree_stats_fields_are_int() -> None:
    stats = CopyTreeStats()
    assert_type(stats.dirs, int)
    assert_type(stats.files, int)
    assert_type(stats.symlinks, int)
    assert_type(stats.bytes, int)
