# SPDX-License-Identifier: LGPL-3.0-or-later

"""Tests for create_idmap_mapping / create_idmap_userns / idmap_userns."""

import contextlib
import errno
import multiprocessing
import os
import subprocess
import tempfile
import threading
import time

import pytest

import truenas_os
from truenas_os_pyutils.namespace import idmap_userns


UINT32_MAX = 0xFFFFFFFF
NEEDS_ROOT = pytest.mark.skipif(
    os.geteuid() != 0,
    reason="Requires CAP_SYS_ADMIN to write non-identity uid/gid maps",
)


# ── create_idmap_mapping: validation surface ────────────────────────────────

def test_create_idmap_mapping_happy_path():
    e = truenas_os.create_idmap_mapping(0, 100000, 65536)
    assert isinstance(e, truenas_os.IdmapMappingEntry)
    assert e.inside == 0
    assert e.outside == 100000
    assert e.length == 65536
    # Tuple-style access also works
    assert e[0] == 0
    assert e[1] == 100000
    assert e[2] == 65536
    assert tuple(e) == (0, 100000, 65536)


def test_create_idmap_mapping_at_boundaries():
    # UINT32_MAX with length=1 is exactly representable
    e = truenas_os.create_idmap_mapping(UINT32_MAX, UINT32_MAX, 1)
    assert e.inside == UINT32_MAX
    assert e.outside == UINT32_MAX
    assert e.length == 1


def test_create_idmap_mapping_negative_inside():
    with pytest.raises((ValueError, OverflowError)):
        truenas_os.create_idmap_mapping(-1, 0, 1)


def test_create_idmap_mapping_negative_outside():
    with pytest.raises((ValueError, OverflowError)):
        truenas_os.create_idmap_mapping(0, -1, 1)


def test_create_idmap_mapping_negative_length():
    with pytest.raises((ValueError, OverflowError)):
        truenas_os.create_idmap_mapping(0, 0, -1)


def test_create_idmap_mapping_zero_length():
    with pytest.raises(ValueError):
        truenas_os.create_idmap_mapping(0, 0, 0)


def test_create_idmap_mapping_inside_overflow():
    with pytest.raises(ValueError):
        truenas_os.create_idmap_mapping(UINT32_MAX, 0, 2)


def test_create_idmap_mapping_outside_overflow():
    with pytest.raises(ValueError):
        truenas_os.create_idmap_mapping(0, UINT32_MAX, 2)


def test_create_idmap_mapping_above_uint32_max():
    with pytest.raises((ValueError, OverflowError)):
        truenas_os.create_idmap_mapping(UINT32_MAX + 1, 0, 1)


def test_create_idmap_mapping_non_int():
    with pytest.raises(TypeError):
        truenas_os.create_idmap_mapping("0", 0, 1)
    with pytest.raises(TypeError):
        truenas_os.create_idmap_mapping(0, "0", 1)
    with pytest.raises(TypeError):
        truenas_os.create_idmap_mapping(0, 0, "1")


def test_create_idmap_mapping_wrong_arg_count():
    with pytest.raises(TypeError):
        truenas_os.create_idmap_mapping(0, 0)  # too few
    with pytest.raises(TypeError):
        truenas_os.create_idmap_mapping(0, 0, 1, 1)  # too many


# ── create_idmap_userns: type strictness ────────────────────────────────────

def test_create_idmap_userns_symbols_importable():
    assert hasattr(truenas_os, "create_idmap_userns")
    assert hasattr(truenas_os, "create_idmap_mapping")
    assert hasattr(truenas_os, "IdmapMappingEntry")


def test_create_idmap_userns_rejects_raw_tuples():
    with pytest.raises(TypeError, match="IdmapMappingEntry"):
        truenas_os.create_idmap_userns(
            uid_map=[(0, 0, 1)],
            gid_map=[(0, 0, 1)],
        )


def test_create_idmap_userns_rejects_empty():
    e = truenas_os.create_idmap_mapping(os.geteuid(), os.geteuid(), 1)
    with pytest.raises(ValueError, match="non-empty"):
        truenas_os.create_idmap_userns(uid_map=[], gid_map=[e])
    with pytest.raises(ValueError, match="non-empty"):
        truenas_os.create_idmap_userns(uid_map=[e], gid_map=[])


def test_create_idmap_userns_requires_kwonly():
    e = truenas_os.create_idmap_mapping(os.geteuid(), os.geteuid(), 1)
    with pytest.raises(TypeError):
        truenas_os.create_idmap_userns([e], [e])  # positional should fail


# ── create_idmap_userns: kernel surface ─────────────────────────────────────

def test_create_idmap_userns_identity_map():
    """Identity map (caller's own uid/gid → same) works without elevated caps."""
    uid_entry = truenas_os.create_idmap_mapping(os.geteuid(), os.geteuid(), 1)
    gid_entry = truenas_os.create_idmap_mapping(os.getegid(), os.getegid(), 1)
    fd = truenas_os.create_idmap_userns(uid_map=[uid_entry], gid_map=[gid_entry])
    try:
        assert fd >= 0
        link = os.readlink(f"/proc/self/fd/{fd}")
        assert link.startswith("user:[")
    finally:
        os.close(fd)


@NEEDS_ROOT
def test_create_idmap_userns_real_range():
    """Non-identity map covering the typical container UID range."""
    e = truenas_os.create_idmap_mapping(0, 100000, 65536)
    fd = truenas_os.create_idmap_userns(uid_map=[e], gid_map=[e])
    try:
        assert fd >= 0
        link = os.readlink(f"/proc/self/fd/{fd}")
        assert link.startswith("user:[")
    finally:
        os.close(fd)


@NEEDS_ROOT
def test_create_idmap_userns_multiple_ranges():
    """Multiple ranges per map."""
    uid_entries = [
        truenas_os.create_idmap_mapping(0, 100000, 1000),
        truenas_os.create_idmap_mapping(1000, 200000, 1000),
    ]
    gid_entries = [
        truenas_os.create_idmap_mapping(0, 100000, 1000),
    ]
    fd = truenas_os.create_idmap_userns(uid_map=uid_entries, gid_map=gid_entries)
    try:
        assert fd >= 0
    finally:
        os.close(fd)


def test_create_idmap_userns_unprivileged_non_identity_fails():
    """Mapping IDs outside the caller's own without CAP_SYS_ADMIN should fail."""
    if os.geteuid() == 0:
        pytest.skip("Running as root; non-identity maps succeed")
    e = truenas_os.create_idmap_mapping(0, 100000, 65536)
    with pytest.raises(OSError):
        truenas_os.create_idmap_userns(uid_map=[e], gid_map=[e])

    # No zombie children should remain
    deadline = time.time() + 1.0
    while time.time() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            break
        time.sleep(0.01)


# ── idmap_userns context manager ────────────────────────────────────────────

def test_idmap_userns_context_manager_yields_open_fd():
    uid_entry = truenas_os.create_idmap_mapping(os.geteuid(), os.geteuid(), 1)
    gid_entry = truenas_os.create_idmap_mapping(os.getegid(), os.getegid(), 1)
    captured_fd = None
    with idmap_userns([uid_entry], [gid_entry]) as fd:
        captured_fd = fd
        assert fd >= 0
        # fstat should succeed inside the with-block
        os.fstat(fd)

    # fstat must fail after exit — fd is closed
    with pytest.raises(OSError) as exc:
        os.fstat(captured_fd)
    assert exc.value.errno == errno.EBADF


def test_idmap_userns_rejects_raw_tuples_at_c_boundary():
    """Pyutils wrapper passes through to C, which rejects raw tuples."""
    with pytest.raises(TypeError):
        with idmap_userns([(0, 0, 1)], [(0, 0, 1)]):
            pass


# ── GIL released during the dance ───────────────────────────────────────────

def test_create_idmap_userns_releases_gil():
    """A background thread should make forward progress while
    create_idmap_userns is executing the syscall sequence."""
    progress = {"ticks": 0, "stop": False}

    def ticker():
        while not progress["stop"]:
            progress["ticks"] += 1

    t = threading.Thread(target=ticker, daemon=True)
    t.start()
    try:
        time.sleep(0.05)  # let ticker warm up
        baseline = progress["ticks"]

        uid_entry = truenas_os.create_idmap_mapping(os.geteuid(), os.geteuid(), 1)
        gid_entry = truenas_os.create_idmap_mapping(os.getegid(), os.getegid(), 1)
        # Run create_idmap_userns several times; ticker should keep going.
        for _ in range(20):
            fd = truenas_os.create_idmap_userns(
                uid_map=[uid_entry], gid_map=[gid_entry],
            )
            os.close(fd)

        # ticker must have made progress
        assert progress["ticks"] > baseline
    finally:
        progress["stop"] = True
        t.join(timeout=1.0)


# ── Functional: truenas_pylibvirt bind-mount-with-idmap pattern ─────────────
#
# These exercise the full syscall sequence that truenas_pylibvirt uses to set
# up an idmapped container rootfs:
#
#   userns_fd = create_idmap_userns(uid_map, gid_map)
#   tree_fd   = open_tree(OPEN_TREE_CLONE | OPEN_TREE_CLOEXEC, source)
#   mount_setattr(tree_fd, attr_set=MOUNT_ATTR_IDMAP, userns_fd=userns_fd)
#   mount_setattr(tree_fd, propagation=MS_SLAVE)
#   move_mount(tree_fd, target)
#
# Run against ZFS datasets with both acltypes (posixacl, nfsv4) via the
# conftest fixtures, since IDmapped mount support is exercised by VFS but
# the source filesystem's behaviour matters for the consumer.

_HOST_BASE  = 100000
_HOST_RANGE = 65536


@contextlib.contextmanager
def _idmapped_bind(source, host_base=_HOST_BASE, host_range=_HOST_RANGE):
    """Yield a target directory bound from `source` with an idmap that maps
    inside [0, host_range) → outside [host_base, host_base+host_range).
    Cleans up on exit.

    The target lives at `<source>/.idmap_target/` — a subdirectory of the
    source mount itself. This keeps the bind inside the (private-propagation)
    dataset mount the conftest fixture set up; placing the target on a
    host-shared mount like /tmp can hit EACCES from move_mount in some
    container/userns-restricted environments.
    """
    e_uid = truenas_os.create_idmap_mapping(0, host_base, host_range)
    e_gid = truenas_os.create_idmap_mapping(0, host_base, host_range)

    target = os.path.join(source, ".idmap_target")
    os.makedirs(target, exist_ok=True)
    try:
        with idmap_userns([e_uid], [e_gid]) as userns_fd:
            tree_fd = truenas_os.open_tree(
                path=source,
                flags=truenas_os.OPEN_TREE_CLONE | truenas_os.OPEN_TREE_CLOEXEC,
            )
            try:
                truenas_os.mount_setattr(
                    path="", dirfd=tree_fd,
                    attr_set=truenas_os.MOUNT_ATTR_IDMAP,
                    userns_fd=userns_fd,
                    flags=truenas_os.AT_EMPTY_PATH,
                )
                truenas_os.mount_setattr(
                    path="", dirfd=tree_fd,
                    propagation=truenas_os.MS_SLAVE,
                    flags=truenas_os.AT_EMPTY_PATH,
                )
                truenas_os.move_mount(
                    from_path="", from_dirfd=tree_fd,
                    to_path=target,
                    flags=truenas_os.MOVE_MOUNT_F_EMPTY_PATH,
                )
            finally:
                os.close(tree_fd)

        try:
            yield target
        finally:
            subprocess.run(["umount", target], capture_output=True, check=False)
    finally:
        with contextlib.suppress(OSError):
            os.rmdir(target)


def _assert_idmap_remaps_ownership(source, target):
    """Helper: file owned by root inside `source` is observed with the
    host-mapped uid/gid through `target`."""
    src_file = os.path.join(source, "owned_by_root")
    with open(src_file, "w") as f:
        f.write("hello")
    os.chown(src_file, 0, 0)

    src_stat = os.stat(src_file)
    assert src_stat.st_uid == 0
    assert src_stat.st_gid == 0

    target_file = os.path.join(target, "owned_by_root")
    target_stat = os.stat(target_file)
    assert target_stat.st_uid == _HOST_BASE, (
        f"expected uid {_HOST_BASE} through idmapped mount, got {target_stat.st_uid}"
    )
    assert target_stat.st_gid == _HOST_BASE, (
        f"expected gid {_HOST_BASE} through idmapped mount, got {target_stat.st_gid}"
    )

    # File contents must still be readable through the idmapped bind.
    with open(target_file) as f:
        assert f.read() == "hello"


@NEEDS_ROOT
def test_idmapped_bind_posix_dataset(posix_dataset):
    """Bind-mount-with-idmap against a ZFS dataset with acltype=posixacl.

    Mirrors the truenas_pylibvirt container-rootfs setup pattern; this is the
    main case used in production for unprivileged Linux containers.
    """
    with _idmapped_bind(posix_dataset) as target:
        _assert_idmap_remaps_ownership(posix_dataset, target)


@NEEDS_ROOT
def test_idmapped_bind_nfs4_dataset(nfs4_dataset):
    """Bind-mount-with-idmap against a ZFS dataset with acltype=nfsv4.

    NFSv4 ACL datasets have a different in-kernel ACL plumbing than posixacl;
    this test guards against regressions specific to that interaction path.
    """
    with _idmapped_bind(nfs4_dataset) as target:
        _assert_idmap_remaps_ownership(nfs4_dataset, target)


def _create_as_mapped_user(path):
    """multiprocessing worker: drop to _HOST_BASE creds and create `path`.

    Run in a forked child so credential drops don't affect the test runner.
    Raises propagate as a non-zero exitcode in the parent.
    """
    # Drop GID first while we still hold CAP_SETGID; once euid is non-root
    # setresgid would be denied.
    os.setresgid(_HOST_BASE, _HOST_BASE, _HOST_BASE)
    os.setresuid(_HOST_BASE, _HOST_BASE, _HOST_BASE)
    with open(path, "w") as f:
        f.write("world")


@NEEDS_ROOT
def test_idmapped_bind_create_and_chown_writes_back(posix_dataset):
    """Create a file *through* the idmapped mount; verify the host-side uid
    on the underlying dataset corresponds correctly.

    Idmapped mounts reject file creation when the creator's host uid is not
    in the outside range of the map (the kernel returns EOVERFLOW from
    `from_vfsuid()`), so host root (uid 0, unmapped) cannot create files
    through this bind directly. Spawn a forked subprocess that drops
    credentials to `_HOST_BASE` (which the map sends to inside-uid 0) and
    have it do the open(). The file should then appear:
      - through the idmapped bind: owned by uid/gid `_HOST_BASE`
      - on the underlying dataset:   owned by uid/gid 0
    confirming the mapping is bidirectional.
    """
    with _idmapped_bind(posix_dataset) as target:
        created = os.path.join(target, "created_via_idmap")

        # 'fork' start method so the child inherits the test runner's mount
        # namespace (including the idmapped bind we just set up).
        ctx = multiprocessing.get_context("fork")
        proc = ctx.Process(target=_create_as_mapped_user, args=(created,))
        proc.start()
        proc.join()
        assert proc.exitcode == 0, (
            f"child create failed (exitcode={proc.exitcode})"
        )

        # Through the idmapped bind: file appears as the outside (host) IDs.
        assert os.stat(created).st_uid == _HOST_BASE
        assert os.stat(created).st_gid == _HOST_BASE

        # Directly on the underlying dataset: file is stored as inside IDs.
        underlying = os.path.join(posix_dataset, "created_via_idmap")
        assert os.stat(underlying).st_uid == 0
        assert os.stat(underlying).st_gid == 0
