# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Tests for truenas_os.fgetxattr / fsetxattr / flistxattr.
#
# Note: the user.* xattr namespace on stock Linux kernels has a hard
# 64 KiB cap on individual values.  Tests that exercise the > 64 KiB
# range are gated on a runtime probe and skipped on the GitHub workflow
# kernel; they only run on a TrueNAS kernel that lifts the limit.

import errno
import os

import pytest
import truenas_os


# ── Probes / fixtures ────────────────────────────────────────────────────────


def _user_xattr_supported(path):
    """Return True if user.* xattrs work at all on this filesystem."""
    fd = os.open(path, os.O_RDWR)
    try:
        truenas_os.fsetxattr(fd, "user.probe", b"x")
        return True
    except OSError as e:
        if e.errno in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM):
            return False
        raise
    finally:
        os.close(fd)


def _value_size_supported(path, size):
    """Return True if a `size`-byte user.* xattr can be set AND read back.

    Many kernels (including stock Linux) cap user.* getxattr at
    XATTR_SIZE_MAX = 64 KiB even when set succeeds at larger sizes; we
    therefore round-trip the probe rather than testing setxattr alone.
    """
    fd = os.open(path, os.O_RDWR)
    try:
        payload = b"x" * size
        try:
            truenas_os.fsetxattr(fd, "user.size_probe", payload)
            got = truenas_os.fgetxattr(fd, "user.size_probe")
            return len(got) == size
        except OSError as e:
            if e.errno in (errno.E2BIG, errno.ENOSPC, errno.EFBIG):
                return False
            raise
        finally:
            try:
                os.removexattr(fd, "user.size_probe")
            except OSError:
                pass
    finally:
        os.close(fd)


@pytest.fixture
def xattr_file(tmp_path):
    """A regular file usable for user.* xattr operations.

    Skips the test if the underlying filesystem does not support user
    xattrs (e.g. some tmpfs builds, certain overlays).
    """
    path = tmp_path / "xfile"
    path.write_bytes(b"")
    if not _user_xattr_supported(str(path)):
        pytest.skip("filesystem does not support user.* xattrs")
    # _user_xattr_supported leaves a 'user.probe' xattr behind; remove it
    # so each test starts clean.
    fd = os.open(str(path), os.O_RDWR)
    try:
        try:
            os.removexattr(fd, "user.probe")
        except OSError:
            pass
    finally:
        os.close(fd)
    return path


@pytest.fixture
def fd(xattr_file):
    fd_ = os.open(str(xattr_file), os.O_RDWR)
    try:
        yield fd_
    finally:
        os.close(fd_)


# ── Module-level constants ────────────────────────────────────────────────────


def test_constants_exposed():
    assert truenas_os.XATTR_CREATE == 1
    assert truenas_os.XATTR_REPLACE == 2
    assert truenas_os.XATTR_SIZE_MAX == 2 * 1024 * 1024


# ── flistxattr ────────────────────────────────────────────────────────────────


def test_flistxattr_empty_returns_empty_list(fd):
    assert truenas_os.flistxattr(fd) == []


def test_flistxattr_returns_set_names(fd):
    truenas_os.fsetxattr(fd, "user.alpha", b"a")
    truenas_os.fsetxattr(fd, "user.beta", b"b")
    truenas_os.fsetxattr(fd, "user.gamma", b"g")
    names = truenas_os.flistxattr(fd)
    assert set(names) == {"user.alpha", "user.beta", "user.gamma"}


def test_flistxattr_growth_path(fd):
    # 32 names * ~24 bytes each = ~768 bytes of NUL-separated names,
    # exceeding the 512-byte initial buffer and forcing the realloc loop.
    expected = set()
    for i in range(32):
        name = f"user.growthtest_{i:02d}"
        truenas_os.fsetxattr(fd, name, b"x")
        expected.add(name)
    assert set(truenas_os.flistxattr(fd)) == expected


# ── fgetxattr ─────────────────────────────────────────────────────────────────


def test_fgetxattr_round_trip_small(fd):
    truenas_os.fsetxattr(fd, "user.test", b"hello world")
    assert truenas_os.fgetxattr(fd, "user.test") == b"hello world"


def test_fgetxattr_round_trip_empty_value(fd):
    truenas_os.fsetxattr(fd, "user.empty", b"")
    assert truenas_os.fgetxattr(fd, "user.empty") == b""


def test_fgetxattr_round_trip_8k(fd):
    """Forces the read loop to grow past the 512-byte initial buffer."""
    payload = bytes((i & 0xFF) for i in range(8 * 1024))
    truenas_os.fsetxattr(fd, "user.eightk", payload)
    assert truenas_os.fgetxattr(fd, "user.eightk") == payload


def test_fgetxattr_missing_raises_enodata(fd):
    with pytest.raises(OSError) as exc:
        truenas_os.fgetxattr(fd, "user.absent")
    assert exc.value.errno == errno.ENODATA


def test_fgetxattr_keyword_args(fd):
    truenas_os.fsetxattr(fd, "user.kw", b"v")
    assert truenas_os.fgetxattr(fd=fd, name="user.kw") == b"v"


# ── fsetxattr ─────────────────────────────────────────────────────────────────


def test_fsetxattr_create_flag_on_existing_raises_eexist(fd):
    truenas_os.fsetxattr(fd, "user.dup", b"first")
    with pytest.raises(OSError) as exc:
        truenas_os.fsetxattr(fd, "user.dup", b"second", flags=truenas_os.XATTR_CREATE)
    assert exc.value.errno == errno.EEXIST
    # Original value preserved.
    assert truenas_os.fgetxattr(fd, "user.dup") == b"first"


def test_fsetxattr_create_flag_on_missing_succeeds(fd):
    truenas_os.fsetxattr(fd, "user.create_ok", b"v", flags=truenas_os.XATTR_CREATE)
    assert truenas_os.fgetxattr(fd, "user.create_ok") == b"v"


def test_fsetxattr_replace_flag_on_missing_raises_enodata(fd):
    with pytest.raises(OSError) as exc:
        truenas_os.fsetxattr(fd, "user.absent", b"v", flags=truenas_os.XATTR_REPLACE)
    assert exc.value.errno == errno.ENODATA


def test_fsetxattr_replace_flag_on_existing_succeeds(fd):
    truenas_os.fsetxattr(fd, "user.replace_ok", b"first")
    truenas_os.fsetxattr(fd, "user.replace_ok", b"second", flags=truenas_os.XATTR_REPLACE)
    assert truenas_os.fgetxattr(fd, "user.replace_ok") == b"second"


def test_fsetxattr_invalid_flags_raises_value_error(fd):
    with pytest.raises(ValueError, match="XATTR_CREATE"):
        truenas_os.fsetxattr(fd, "user.bad", b"v", flags=999)
    with pytest.raises(ValueError, match="XATTR_CREATE"):
        # Combined CREATE|REPLACE is rejected.
        truenas_os.fsetxattr(
            fd, "user.bad", b"v",
            flags=truenas_os.XATTR_CREATE | truenas_os.XATTR_REPLACE,
        )


def test_fsetxattr_oversize_short_circuits(fd):
    """Values > XATTR_SIZE_MAX raise OSError(E2BIG) without hitting the syscall."""
    payload = b"x" * (truenas_os.XATTR_SIZE_MAX + 1)
    with pytest.raises(OSError) as exc:
        truenas_os.fsetxattr(fd, "user.huge", payload)
    assert exc.value.errno == errno.E2BIG


def test_fsetxattr_flags_keyword_only(fd):
    """flags must be passed by keyword, not positionally."""
    with pytest.raises(TypeError):
        truenas_os.fsetxattr(fd, "user.kw", b"v", truenas_os.XATTR_CREATE)


# ── boundary stress ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("size", [256, 512, 513, 1024, 4096, 32 * 1024, 64 * 1024 - 16])
def test_round_trip_at_boundary(fd, size):
    """Round-trip values around the realloc-loop boundaries."""
    payload = bytes((i & 0xFF) for i in range(size))
    truenas_os.fsetxattr(fd, "user.boundary", payload)
    assert truenas_os.fgetxattr(fd, "user.boundary") == payload
    # Reset for next parametrisation.
    os.removexattr(fd, "user.boundary")


# ── Large-value tests (TrueNAS kernels with lifted user.* xattr cap) ─────────
#
# Stock Linux (and some TrueNAS kernels) cap user.* getxattr at
# XATTR_SIZE_MAX = 64 KiB.  The tests below probe round-trip support at
# the exact size being tested and skip on kernels where it is not
# available — covering both the GitHub workflow runner and any TrueNAS
# build that has not lifted the limit.


def _make_large_fd(tmp_path, size):
    path = tmp_path / f"xfile_large_{size}"
    path.write_bytes(b"")
    if not _user_xattr_supported(str(path)):
        pytest.skip("filesystem does not support user.* xattrs")
    if not _value_size_supported(str(path), size):
        pytest.skip(f"kernel does not support user.* xattrs at {size} bytes")
    fd_ = os.open(str(path), os.O_RDWR)
    try:
        os.removexattr(fd_, "user.probe")
    except OSError:
        pass
    return fd_


def test_round_trip_1mib(tmp_path):
    fd_ = _make_large_fd(tmp_path, 1024 * 1024)
    try:
        payload = bytes((i & 0xFF) for i in range(1024 * 1024))
        truenas_os.fsetxattr(fd_, "user.onemib", payload)
        assert truenas_os.fgetxattr(fd_, "user.onemib") == payload
    finally:
        os.close(fd_)


def test_round_trip_2mib_max(tmp_path):
    fd_ = _make_large_fd(tmp_path, truenas_os.XATTR_SIZE_MAX)
    try:
        payload = b"y" * truenas_os.XATTR_SIZE_MAX
        truenas_os.fsetxattr(fd_, "user.maxsize", payload)
        assert truenas_os.fgetxattr(fd_, "user.maxsize") == payload
    finally:
        os.close(fd_)
