# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Tests for truenas_os_pyutils.truenas_shutil.copytree — recursive directory tree
# copy.  The walker is driven by truenas_os.iter_filesystem_contents (fsiter)
# which validates the source against statmount, so all tests use real
# directories on a real mount (tmp_path or a ZFS dataset fixture).
import errno
import os
import random
import stat
from operator import eq, ne

import pytest

from truenas_os_pyutils.truenas_shutil import (
    DEF_CP_FLAGS,
    CopyFlags,
    CopyTreeConfig,
    CopyTreeOp,
    CopyTreeStats,
    copytree,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_tree(root):
    """Create a small but representative source tree under ``root``.

    Layout:
        root/
            a.txt        (10 bytes)
            b.bin        (4 KiB)
            sub/
                nested.txt (5 bytes)
            empty/
            link -> a.txt
    """
    (root / "a.txt").write_text("hello world")
    (root / "b.bin").write_bytes(b"\x00" * 4096)
    sub = root / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("nest!")
    (root / "empty").mkdir()
    os.symlink("a.txt", str(root / "link"))


def _count_paths(root):
    dirs, files, links = 0, 0, 0
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        dirs += len(dirnames)
        for f in filenames:
            full = os.path.join(dirpath, f)
            if os.path.islink(full):
                links += 1
            else:
                files += 1
    return dirs, files, links


# ── Golden path ───────────────────────────────────────────────────────────────


def test_copytree_basic(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _build_tree(src)
    dst = tmp_path / "dst"

    stats = copytree(str(src), str(dst), CopyTreeConfig())

    assert isinstance(stats, CopyTreeStats)
    assert (dst / "a.txt").read_text() == "hello world"
    assert (dst / "b.bin").read_bytes() == b"\x00" * 4096
    assert (dst / "sub" / "nested.txt").read_text() == "nest!"
    assert (dst / "empty").is_dir()
    assert os.path.islink(str(dst / "link"))
    assert os.readlink(str(dst / "link")) == "a.txt"


def test_copytree_stats_match_tree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _build_tree(src)
    dst = tmp_path / "dst"

    expected_dirs, expected_files, expected_links = _count_paths(src)
    stats = copytree(str(src), str(dst), CopyTreeConfig())

    assert stats.dirs == expected_dirs
    assert stats.files == expected_files
    assert stats.symlinks == expected_links
    # bytes should equal the sum of regular file sizes
    expected_bytes = sum(
        os.stat(os.path.join(dp, f)).st_size
        for dp, _, files in os.walk(str(src), followlinks=False)
        for f in files
        if not os.path.islink(os.path.join(dp, f))
    )
    assert stats.bytes == expected_bytes


# ── ValueError for relative paths ─────────────────────────────────────────────


def test_copytree_relative_src_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(str(tmp_path))
    with pytest.raises(ValueError, match="absolute path"):
        copytree("rel_src", str(tmp_path / "dst"), CopyTreeConfig())


def test_copytree_relative_dst_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(ValueError, match="absolute path"):
        copytree(str(src), "rel_dst", CopyTreeConfig())


# ── exist_ok semantics ───────────────────────────────────────────────────────


def test_copytree_exist_ok_false_raises_on_existing_dst(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()  # destination already exists

    with pytest.raises(FileExistsError):
        copytree(str(src), str(dst), CopyTreeConfig(exist_ok=False))


def test_copytree_exist_ok_true_overwrites(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a").write_text("new contents")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "a").write_text("old contents")

    copytree(str(src), str(dst), CopyTreeConfig(exist_ok=True))
    assert (dst / "a").read_text() == "new contents"


# ── CopyTreeOp variants ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "op",
    [CopyTreeOp.DEFAULT, CopyTreeOp.SENDFILE, CopyTreeOp.USERSPACE],
)
def test_copytree_op_variants_succeed(tmp_path, op):
    src = tmp_path / "src"
    src.mkdir()
    _build_tree(src)
    dst = tmp_path / f"dst_{op.name.lower()}"

    copytree(str(src), str(dst), CopyTreeConfig(op=op))
    assert (dst / "a.txt").read_text() == "hello world"
    assert (dst / "sub" / "nested.txt").read_text() == "nest!"


def test_copytree_op_clone_succeeds_or_raises_xdev(tmp_path):
    # CLONE has no userspace fallback; on filesystems that don't support
    # copy_file_range it should raise.  On tmpfs it typically does support
    # it via reflink-style cloning.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("clone test")
    dst = tmp_path / "dst"

    try:
        copytree(str(src), str(dst), CopyTreeConfig(op=CopyTreeOp.CLONE))
    except OSError as e:
        # Acceptable: filesystem doesn't support clone for this combo
        assert e.errno in (
            __import__("errno").EXDEV,
            __import__("errno").ENOTSUP,
            __import__("errno").EOPNOTSUPP,
            __import__("errno").EINVAL,
        )
    else:
        assert (dst / "a.txt").read_text() == "clone test"


# ── CopyFlags combinations ───────────────────────────────────────────────────


def test_copytree_no_flags_copies_data_only(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "a.txt"
    f.write_text("data")
    os.chmod(str(f), 0o741)

    dst = tmp_path / "dst"
    copytree(str(src), str(dst), CopyTreeConfig(flags=CopyFlags(0)))
    # Mode is NOT preserved because PERMISSIONS bit is unset.
    assert (dst / "a.txt").read_text() == "data"
    # The destination file was created with default mode (umask-applied),
    # so it should NOT match 0o741.
    assert stat.S_IMODE(os.stat(str(dst / "a.txt")).st_mode) != 0o741


def test_copytree_permissions_flag_preserves_mode(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "a.txt"
    f.write_text("data")
    os.chmod(str(f), 0o741)
    dst = tmp_path / "dst"

    copytree(str(src), str(dst), CopyTreeConfig(flags=CopyFlags.PERMISSIONS))

    assert stat.S_IMODE(os.stat(str(dst / "a.txt")).st_mode) == 0o741


def test_copytree_owner_flag_preserves_uid_gid(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("owner-preservation test needs root to chown")
    src = tmp_path / "src"
    src.mkdir()
    f = src / "a.txt"
    f.write_text("data")
    os.chown(str(f), 12345, 12346)
    dst = tmp_path / "dst"

    copytree(str(src), str(dst), CopyTreeConfig(flags=CopyFlags.OWNER))

    st = os.stat(str(dst / "a.txt"))
    assert st.st_uid == 12345
    assert st.st_gid == 12346


def test_copytree_timestamps_flag_preserves_mtime(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "a.txt"
    f.write_text("data")
    # Set known timestamps (seconds; ns gets bucketed by filesystem)
    target_mtime_ns = 1_700_000_000 * 10**9
    target_atime_ns = 1_600_000_000 * 10**9
    os.utime(str(f), ns=(target_atime_ns, target_mtime_ns))
    dst = tmp_path / "dst"

    copytree(str(src), str(dst), CopyTreeConfig(flags=CopyFlags.TIMESTAMPS))

    st = os.stat(str(dst / "a.txt"))
    # Filesystems may truncate ns precision, so allow a small window.
    assert abs(st.st_mtime_ns - target_mtime_ns) < 10**6
    assert abs(st.st_atime_ns - target_atime_ns) < 10**6


def test_copytree_default_flags_is_all_four():
    # DEF_CP_FLAGS should set all four metadata bits.
    cfg = CopyTreeConfig()
    assert cfg.flags & CopyFlags.PERMISSIONS
    assert cfg.flags & CopyFlags.XATTRS
    assert cfg.flags & CopyFlags.OWNER
    assert cfg.flags & CopyFlags.TIMESTAMPS


# ── Empty source ──────────────────────────────────────────────────────────────


def test_copytree_empty_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"

    stats = copytree(str(src), str(dst), CopyTreeConfig())

    assert dst.is_dir()
    assert stats.dirs == 0
    assert stats.files == 0
    assert stats.symlinks == 0
    assert stats.bytes == 0


# ── reporting_callback (passed straight through to fsiter) ───────────────────


def test_copytree_invokes_reporting_callback(tmp_path):
    """The callback is forwarded to fsiter unchanged.  Build a tree large
    enough that fsiter's increment fires at least once."""
    src = tmp_path / "src"
    src.mkdir()
    # 2000 entries should be well above the default reporting_increment=1000.
    sub = src / "many"
    sub.mkdir()
    for i in range(2000):
        (sub / f"f{i}").write_bytes(b"x")
    dst = tmp_path / "dst"

    calls = []

    def cb(dir_stack, state, private):
        calls.append((len(dir_stack), state.cnt, private))

    sentinel = object()
    copytree(
        str(src),
        str(dst),
        CopyTreeConfig(
            reporting_callback=cb,
            reporting_private_data=sentinel,
            reporting_increment=500,
        ),
    )

    assert calls, "reporting_callback was never invoked"
    # Private data should be forwarded verbatim.
    assert all(private is sentinel for _, _, private in calls)


def test_copytree_no_callback_runs_silently(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _build_tree(src)
    dst = tmp_path / "dst"
    # Default config has reporting_callback=None — should just work.
    stats = copytree(str(src), str(dst), CopyTreeConfig())
    assert stats.files >= 1


# ── ZFS-only behavior ────────────────────────────────────────────────────────


@pytest.fixture
def zfs_dataset(zpool, zfs_enabled, request):
    """Per-test ZFS dataset with acltype=posixacl; skip if ZFS unavailable."""
    if not zfs_enabled or zpool is None:
        pytest.skip("ZFS or root unavailable")
    from tests.conftest import _zfs_create_dataset, _zfs_destroy_dataset
    ds, mountpoint = _zfs_create_dataset(zpool, "posixacl")
    yield mountpoint
    _zfs_destroy_dataset(ds, mountpoint)


def test_copytree_skips_zfs_ctldir(zfs_dataset, tmp_path):
    """`.zfs` ctldir is yielded by fsiter on ZFS but must not be copied."""
    import subprocess
    # Make a snapshot so .zfs/snapshot is populated.
    src = os.path.join(zfs_dataset, "src")
    os.makedirs(src)
    (open(os.path.join(src, "a.txt"), "w")).write("data")
    # Set snapdir=visible so .zfs is enumerable.
    pool_ds = subprocess.run(
        ["zfs", "list", "-H", "-o", "name", zfs_dataset],
        capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["zfs", "set", "snapdir=visible", pool_ds], check=True)
    subprocess.run(["zfs", "snapshot", f"{pool_ds}@s1"], check=True)
    try:
        dst = os.path.join(zfs_dataset, "dst")
        copytree(src, dst, CopyTreeConfig())

        # .zfs must NOT have been copied into dst
        assert not os.path.exists(os.path.join(dst, ".zfs"))
        assert os.path.isfile(os.path.join(dst, "a.txt"))
    finally:
        subprocess.run(
            ["zfs", "destroy", f"{pool_ds}@s1"], check=False
        )


# ── Rich-tree end-to-end coverage ────────────────────────────────────────────
#
# These tests populate a SOURCE tree with non-default uid/gid, mode bits,
# user.* xattrs, mtime, and symlinks at every level, then copy it and
# walk dst recursively asserting that each metadata category is preserved
# (or, when its CopyFlag is unset, NOT preserved).  Without this any
# bug that simply leaves dst at its create-time defaults would slip
# through tests that only check the requested metadata was preserved.

_SEED = 8675309
_DATA_SZ = 128 * 1024
_XATTR_SZ = 1024
_NONROOT_UID = 8675309
_NONROOT_GID = 8675310

_TEST_FILES = ("testfile1", "testfile2", "canary", "alpha_42")
_TEST_DIRS = ("subdir1", "subdir2", "alpha_99")
_FILE_XATTRS = ("user.fxat1", "user.fxat2", "user.fxat3")
_DIR_XATTRS = ("user.dxat1", "user.dxat2", "user.dxat3")


def _user_xattrs_supported(path):
    """True if user.* xattrs are accepted on the filesystem under `path`."""
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.setxattr(fd, "user._probe", b"x")
        return True
    except OSError as e:
        if e.errno in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM):
            return False
        raise
    finally:
        try:
            os.removexattr(fd, "user._probe")
        except OSError:
            pass
        os.close(fd)


def _populate_files(parent, symlink_target, rng):
    for name in _TEST_FILES:
        path = parent / name
        path.write_bytes(rng.randbytes(_DATA_SZ))
        os.chmod(str(path), 0o666)
        os.chown(str(path), _NONROOT_UID, _NONROOT_GID + 1)
        for xat in _FILE_XATTRS:
            os.setxattr(str(path), xat, rng.randbytes(_XATTR_SZ))
        os.symlink(str(symlink_target), str(parent / f"{name}_sl"))
        # utime last so prior ops don't bump mtime/atime.
        os.utime(str(path), ns=(_SEED + 1, _SEED + 2))


@pytest.fixture
def rich_source_tree(tmp_path):
    """Populate tmp_path/SOURCE with files+dirs+symlinks, distinct
    uid/gid, mode bits, xattrs, and mtime at every level."""
    if os.geteuid() != 0:
        pytest.skip("rich-tree fixture needs root for chown")
    if not _user_xattrs_supported(tmp_path):
        pytest.skip("filesystem does not support user.* xattrs")

    rng = random.Random(_SEED)
    source = tmp_path / "SOURCE"
    source.mkdir()
    for xat in _DIR_XATTRS:
        os.setxattr(str(source), xat, rng.randbytes(_XATTR_SZ))
    os.chown(str(source), _NONROOT_UID + 10, _NONROOT_GID + 10)
    os.chmod(str(source), 0o777)

    _populate_files(source, tmp_path, rng)

    for dirname in _TEST_DIRS:
        sub = source / dirname
        sub.mkdir()
        os.chmod(str(sub), 0o777)
        os.chown(str(sub), _NONROOT_UID, _NONROOT_GID)
        for xat in _DIR_XATTRS:
            os.setxattr(str(sub), xat, rng.randbytes(_XATTR_SZ))
        # External target dir for this level's symlinks (outside SOURCE).
        ext = tmp_path / dirname
        ext.mkdir()
        _populate_files(sub, ext, rng)
        os.symlink(str(ext), str(sub / f"{dirname}_sl"))
        os.utime(str(sub), ns=(_SEED + 3, _SEED + 4))

    os.utime(str(source), ns=(_SEED + 5, _SEED + 6))
    return source


def _validate_attrs(src, dst, flags):
    s = os.lstat(str(src))
    d = os.lstat(str(dst))
    assert s.st_size == d.st_size

    if stat.S_ISLNK(s.st_mode):
        assert os.readlink(str(src)) == os.readlink(str(dst))
        return

    op = eq if flags & CopyFlags.OWNER else ne
    assert op(s.st_uid, d.st_uid), f"uid: src={s.st_uid} dst={d.st_uid}"
    assert op(s.st_gid, d.st_gid), f"gid: src={s.st_gid} dst={d.st_gid}"

    op = eq if flags & CopyFlags.PERMISSIONS else ne
    assert op(s.st_mode, d.st_mode), (
        f"mode: src={oct(s.st_mode)} dst={oct(d.st_mode)}"
    )

    op = eq if flags & CopyFlags.TIMESTAMPS else ne
    # mtime is sufficient — atime gets bumped by reads in the test runner.
    assert op(s.st_mtime_ns, d.st_mtime_ns), (
        f"mtime: src={s.st_mtime_ns} dst={d.st_mtime_ns}"
    )


def _validate_xattrs(src, dst, flags):
    if stat.S_ISLNK(os.lstat(str(src)).st_mode):
        return
    src_xs = os.listxattr(str(src))
    dst_xs = os.listxattr(str(dst))
    if flags & CopyFlags.XATTRS:
        assert sorted(src_xs) == sorted(dst_xs)
        for name in src_xs:
            assert os.getxattr(str(src), name) == os.getxattr(str(dst), name)
    else:
        assert src_xs, "fixture should have set xattrs on src"
        assert dst_xs == [], f"unexpected xattrs on dst: {dst_xs}"


def _validate_data(src, dst):
    mode = os.lstat(str(src)).st_mode
    if stat.S_ISLNK(mode):
        return  # readlink covered in _validate_attrs
    if stat.S_ISDIR(mode):
        assert set(os.listdir(str(src))) == set(os.listdir(str(dst)))
        return
    with open(str(src), "rb") as f, open(str(dst), "rb") as g:
        assert f.read() == g.read()


def _validate_tree(src, dst, flags):
    """Recursively assert dst mirrors src, with eq/ne metadata per flag."""
    with os.scandir(str(src)) as it:
        for entry in it:
            child_src = src / entry.name
            child_dst = dst / entry.name
            _validate_data(child_src, child_dst)
            _validate_xattrs(child_src, child_dst, flags)
            _validate_attrs(child_src, child_dst, flags)
            if entry.is_dir() and not entry.is_symlink():
                _validate_tree(child_src, child_dst, flags)
    _validate_data(src, dst)
    _validate_xattrs(src, dst, flags)
    _validate_attrs(src, dst, flags)


def test_copytree_e2e_default_flags_preserve_everything(
    rich_source_tree, tmp_path
):
    src = rich_source_tree
    dst = tmp_path / "DEST"
    cfg = CopyTreeConfig()
    assert cfg.flags == DEF_CP_FLAGS
    stats = copytree(str(src), str(dst), cfg)
    _validate_tree(src, dst, cfg.flags)
    assert stats.files > 0
    assert stats.dirs > 0
    assert stats.symlinks > 0


@pytest.mark.parametrize("flag", [
    CopyFlags.XATTRS,
    CopyFlags.PERMISSIONS,
    CopyFlags.TIMESTAMPS,
    CopyFlags.OWNER,
])
def test_copytree_e2e_per_flag(rich_source_tree, tmp_path, flag):
    """Each individual flag preserves only its own metadata category."""
    src = rich_source_tree
    dst = tmp_path / "DEST"
    copytree(str(src), str(dst), CopyTreeConfig(flags=flag))
    _validate_tree(src, dst, flag)


def test_copytree_e2e_userspace_op_full_tree(rich_source_tree, tmp_path):
    """Forcing USERSPACE op still copies the full rich tree correctly."""
    src = rich_source_tree
    dst = tmp_path / "DEST"
    copytree(
        str(src), str(dst),
        CopyTreeConfig(op=CopyTreeOp.USERSPACE),
    )
    _validate_tree(src, dst, DEF_CP_FLAGS)


@pytest.mark.parametrize("existok", [True, False])
def test_copytree_e2e_existok(rich_source_tree, tmp_path, existok):
    src = rich_source_tree
    dst = tmp_path / "DEST"
    dst.mkdir()
    cfg = CopyTreeConfig(exist_ok=existok)
    if existok:
        copytree(str(src), str(dst), cfg)
        _validate_tree(src, dst, cfg.flags)
    else:
        with pytest.raises(FileExistsError):
            copytree(str(src), str(dst), cfg)


def test_copytree_into_itself_simple(rich_source_tree):
    """dst is a direct subdirectory of src — homedir-misset scenario.

    The recursion guard in `_is_dst_into_self` must prevent infinite
    recursion: `SOURCE/DEST/DEST` must not appear.
    """
    src = rich_source_tree
    dst = src / "DEST"
    copytree(str(src), str(dst), CopyTreeConfig())
    assert not (src / "DEST" / "DEST").exists()


def test_copytree_into_itself_deeper(rich_source_tree):
    """dst is a deeper subdirectory of src; guard fires mid-tree."""
    src = rich_source_tree
    intermediate = src / "FOO" / "BAR"
    intermediate.mkdir(parents=True)
    dst = intermediate / "DEST"
    copytree(str(src), str(dst), CopyTreeConfig())
    # Everything up to (but not into) the destination should exist.
    assert (dst / "FOO" / "BAR").exists()
    assert not (dst / "FOO" / "BAR" / "DEST").exists()
