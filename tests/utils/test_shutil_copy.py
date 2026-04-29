# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Tests for truenas_os_pyutils.shutil.copy — the file-level copy/clone
# primitives that operate on open file descriptors.
import errno
import os
import random
import stat
from unittest import mock

import pytest

from truenas_os_pyutils.shutil.copy import (
    ACCESS_ACL_XATTRS,
    ACL_XATTRS,
    MAX_RW_SZ,
    clone_file,
    clone_or_copy_file,
    copy_file_userspace,
    copy_permissions,
    copy_sendfile,
    copy_xattrs,
)


# ── Module constants ──────────────────────────────────────────────────────────


def test_max_rw_sz_value():
    # Ported verbatim from middleware: ``2147483647 & ~4096`` — clears bit
    # 12 of INT_MAX so sendfile/copy_file_range stay below the kernel's
    # MAX_RW_COUNT limit.  Value is just over 2 GiB.
    assert MAX_RW_SZ == 2147483647 & ~4096
    assert MAX_RW_SZ > 0


def test_acl_xattrs_constants():
    # ACCESS subset must be contained in ACL set.
    assert ACCESS_ACL_XATTRS <= ACL_XATTRS
    assert "system.posix_acl_access" in ACCESS_ACL_XATTRS
    assert "system.nfs4_acl_xdr" in ACCESS_ACL_XATTRS
    # The default POSIX ACL governs new children, not the file itself, so
    # it is intentionally absent from the ACCESS subset.
    assert "system.posix_acl_default" in ACL_XATTRS
    assert "system.posix_acl_default" not in ACCESS_ACL_XATTRS


# ── copy_file_userspace ───────────────────────────────────────────────────────


def test_copy_file_userspace_copies_data(tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    payload = b"hello world\n" * 1000
    src.write_bytes(payload)
    dst.write_bytes(b"")

    src_fd = os.open(str(src), os.O_RDONLY)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        n = copy_file_userspace(src_fd, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    assert n == len(payload)
    assert dst.read_bytes() == payload


def test_copy_file_userspace_empty_file(tmp_path):
    src = tmp_path / "empty.bin"
    dst = tmp_path / "out.bin"
    src.write_bytes(b"")
    dst.write_bytes(b"")
    src_fd = os.open(str(src), os.O_RDONLY)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        assert copy_file_userspace(src_fd, dst_fd) == 0
    finally:
        os.close(src_fd)
        os.close(dst_fd)


# ── copy_sendfile ─────────────────────────────────────────────────────────────


def test_copy_sendfile_copies_data(tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    payload = b"sendfile data\n" * 100
    src.write_bytes(payload)
    dst.write_bytes(b"")

    src_fd = os.open(str(src), os.O_RDONLY)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        n = copy_sendfile(src_fd, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    assert n == len(payload)
    assert dst.read_bytes() == payload


def test_copy_sendfile_falls_back_when_sendfile_returns_zero(tmp_path, monkeypatch):
    # If the kernel cannot do sendfile from this src and the destination is
    # still empty, copy_sendfile should call copy_file_userspace.
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"fallback")
    dst.write_bytes(b"")

    # Simulate sendfile being unavailable for this src/dst combo.
    import truenas_os_pyutils.shutil.copy as mod
    monkeypatch.setattr(mod, "sendfile", lambda *a, **kw: 0)

    src_fd = os.open(str(src), os.O_RDONLY)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        n = copy_sendfile(src_fd, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    assert n == len(b"fallback")
    assert dst.read_bytes() == b"fallback"


# ── clone_file / clone_or_copy_file ───────────────────────────────────────────


def test_clone_file_copies_data_within_same_filesystem(tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    payload = b"clone payload\n" * 100
    src.write_bytes(payload)
    dst.write_bytes(b"")

    src_fd = os.open(str(src), os.O_RDONLY)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        n = clone_file(src_fd, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    assert n == len(payload)
    assert dst.read_bytes() == payload


def test_clone_or_copy_file_falls_back_on_exdev(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    payload = b"xdev fallback\n"
    src.write_bytes(payload)
    dst.write_bytes(b"")

    # Simulate cross-filesystem copy: copy_file_range raises EXDEV.
    import truenas_os_pyutils.shutil.copy as mod

    def fake_copy_file_range(*a, **kw):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(mod, "copy_file_range", fake_copy_file_range)

    src_fd = os.open(str(src), os.O_RDONLY)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        n = clone_or_copy_file(src_fd, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    assert n == len(payload)
    assert dst.read_bytes() == payload


def test_clone_or_copy_file_propagates_other_oserror(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"data")
    dst.write_bytes(b"")

    import truenas_os_pyutils.shutil.copy as mod

    def fake_copy_file_range(*a, **kw):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(mod, "copy_file_range", fake_copy_file_range)

    src_fd = os.open(str(src), os.O_RDONLY)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        with pytest.raises(OSError) as exc_info:
            clone_or_copy_file(src_fd, dst_fd)
        assert exc_info.value.errno == errno.EIO
    finally:
        os.close(src_fd)
        os.close(dst_fd)


# ── copy_permissions ──────────────────────────────────────────────────────────


def test_copy_permissions_no_acl_uses_fchmod(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("a")
    dst.write_text("b")
    os.chmod(str(src), 0o741)
    os.chmod(str(dst), 0o600)

    src_fd = os.open(str(src), os.O_RDWR)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        # Empty xattr list — fchmod path is taken.
        copy_permissions(src_fd, dst_fd, [], 0o741)
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    assert stat.S_IMODE(os.stat(str(dst)).st_mode) == 0o741


def test_copy_permissions_skips_fchmod_when_acl_xattr_listed(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("a")
    dst.write_text("b")
    # Put dst at a known mode; the function should NOT touch it because the
    # source advertises an access ACL xattr (we mock fgetxattr/fsetxattr
    # to avoid needing a real ACL-supporting filesystem).
    os.chmod(str(dst), 0o600)

    src_fd = os.open(str(src), os.O_RDWR)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        with mock.patch("truenas_os_pyutils.shutil.copy.fgetxattr") as g, \
             mock.patch("truenas_os_pyutils.shutil.copy.fsetxattr") as s:
            g.return_value = b"\x00" * 12
            copy_permissions(
                src_fd, dst_fd, ["system.posix_acl_access"], 0o741
            )
            assert g.called and s.called
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    # fchmod should not have been invoked, so dst still has its original mode.
    assert stat.S_IMODE(os.stat(str(dst)).st_mode) == 0o600


# ── copy_xattrs ───────────────────────────────────────────────────────────────


def test_copy_xattrs_skips_acl_and_system(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("a")
    dst.write_text("b")

    src_fd = os.open(str(src), os.O_RDWR)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        with mock.patch("truenas_os_pyutils.shutil.copy.fgetxattr") as g, \
             mock.patch("truenas_os_pyutils.shutil.copy.fsetxattr") as s:
            g.return_value = b"value"
            copy_xattrs(
                src_fd,
                dst_fd,
                [
                    "system.posix_acl_access",  # ACL — skipped
                    "system.nfs4_acl_xdr",  # ACL — skipped
                    "system.something_else",  # other system.* — skipped
                    "user.foo",  # copied
                    "trusted.bar",  # copied
                ],
            )
            assert s.call_count == 2
            copied_names = {call.args[1] for call in s.call_args_list}
            assert copied_names == {"user.foo", "trusted.bar"}
    finally:
        os.close(src_fd)
        os.close(dst_fd)


def test_copy_xattrs_empty_list_is_noop(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("a")
    dst.write_text("b")
    src_fd = os.open(str(src), os.O_RDWR)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        with mock.patch("truenas_os_pyutils.shutil.copy.fsetxattr") as s:
            copy_xattrs(src_fd, dst_fd, [])
            assert s.call_count == 0
    finally:
        os.close(src_fd)
        os.close(dst_fd)


# ── Larger-scale data and full-fallthrough chain ─────────────────────────────


def _write_random_chunks(fd, n_chunks, chunk_sz, seed):
    rng = random.Random(seed)
    for i in range(n_chunks):
        os.pwrite(fd, rng.randbytes(chunk_sz), i * chunk_sz)


def _read_chunks(fd, n_chunks, chunk_sz):
    return [os.pread(fd, chunk_sz, i * chunk_sz) for i in range(n_chunks)]


def test_clone_file_multi_megabyte(tmp_path):
    """Clone a multi-MiB file to exercise the copy_file_range loop body."""
    chunk_sz = 1024 * 1024  # 1 MiB
    n_chunks = 16  # 16 MiB total
    src_fd = os.open(str(tmp_path / "large_src"), os.O_CREAT | os.O_RDWR, 0o600)
    dst_fd = os.open(str(tmp_path / "large_dst"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _write_random_chunks(src_fd, n_chunks, chunk_sz, seed=8675309)
        clone_file(src_fd, dst_fd)
        assert _read_chunks(src_fd, n_chunks, chunk_sz) == _read_chunks(
            dst_fd, n_chunks, chunk_sz
        )
    finally:
        os.close(src_fd)
        os.close(dst_fd)


def test_clone_or_copy_file_full_fallthrough(tmp_path, monkeypatch):
    """Chain clone → sendfile → userspace by forcing each tier to fail."""
    chunk_sz = 1024 * 1024
    n_chunks = 4
    src_fd = os.open(str(tmp_path / "ft_src"), os.O_CREAT | os.O_RDWR, 0o600)
    dst_fd = os.open(str(tmp_path / "ft_dst"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _write_random_chunks(src_fd, n_chunks, chunk_sz, seed=8675309)

        import truenas_os_pyutils.shutil.copy as copy_mod

        def fake_copy_file_range(*a, **kw):
            raise OSError(errno.EXDEV, "MOCK EXDEV")

        monkeypatch.setattr(copy_mod, "copy_file_range", fake_copy_file_range)
        monkeypatch.setattr(copy_mod, "sendfile", lambda *a, **kw: 0)

        clone_or_copy_file(src_fd, dst_fd)

        assert _read_chunks(src_fd, n_chunks, chunk_sz) == _read_chunks(
            dst_fd, n_chunks, chunk_sz
        )
    finally:
        os.close(src_fd)
        os.close(dst_fd)


def test_copy_sendfile_does_not_fall_through_when_sendfile_works(
    tmp_path, monkeypatch
):
    """copy_sendfile must NOT invoke userspace fallback on a happy sendfile."""
    src = tmp_path / "sf_src"
    dst = tmp_path / "sf_dst"
    payload = b"a" * 4096
    src.write_bytes(payload)
    dst.write_bytes(b"")

    import truenas_os_pyutils.shutil.copy as copy_mod

    def boom(*a, **kw):
        raise AssertionError("copy_file_userspace must not be called")

    monkeypatch.setattr(copy_mod, "copy_file_userspace", boom)

    src_fd = os.open(str(src), os.O_RDONLY)
    dst_fd = os.open(str(dst), os.O_RDWR)
    try:
        n = copy_sendfile(src_fd, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    assert n == len(payload)
    assert dst.read_bytes() == payload
