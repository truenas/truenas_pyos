import errno
import os

import pytest

import truenas_os
from truenas_os_pyutils.io import SymlinkInPathError
from truenas_os_pyutils.mount import (
    StatmountResultDict,
    iter_mountinfo,
    statmount,
    umount,
)

# ── statmount ─────────────────────────────────────────────────────────────────

EXPECTED_KEYS = {
    'mount_id', 'parent_id', 'device_id',
    'root', 'mountpoint', 'mount_opts',
    'fs_type', 'mount_source', 'super_opts',
}


def test_statmount_path_returns_dict():
    result = statmount(path='/')
    assert isinstance(result, dict)
    assert EXPECTED_KEYS == result.keys()


def test_statmount_path_device_id_keys():
    result = statmount(path='/')
    assert {'major', 'minor', 'dev_t'} == result['device_id'].keys()


def test_statmount_path_root_mountpoint():
    result = statmount(path='/')
    assert result['mountpoint'] == '/'


def test_statmount_fd():
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = statmount(fd=fd)
        assert result['mountpoint'] == '/'
    finally:
        os.close(fd)


def test_statmount_as_dict_false():
    result = statmount(path='/', as_dict=False)
    assert isinstance(result, truenas_os.StatmountResult)


def test_statmount_neither_raises():
    with pytest.raises(ValueError, match='One of path or fd is required'):
        statmount()


def test_statmount_both_raises():
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match='One of path or fd is required'):
            statmount(path='/', fd=fd)
    finally:
        os.close(fd)


def test_statmount_symlink_raises(tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(target)

    with pytest.raises(SymlinkInPathError) as exc_info:
        statmount(path=str(link))

    assert exc_info.value.errno == errno.ELOOP


def test_statmount_nonexistent_raises():
    with pytest.raises(FileNotFoundError):
        statmount(path='/nonexistent_path_that_cannot_exist_xyz')


# ── iter_mountinfo ────────────────────────────────────────────────────────────

def test_iter_mountinfo_yields_dicts():
    mounts = list(iter_mountinfo())
    assert len(mounts) > 0
    for m in mounts:
        assert EXPECTED_KEYS == m.keys()


def test_iter_mountinfo_as_dict_false():
    mounts = list(iter_mountinfo(as_dict=False))
    assert len(mounts) > 0
    for m in mounts:
        assert isinstance(m, truenas_os.StatmountResult)


def test_iter_mountinfo_contains_root():
    mounts = list(iter_mountinfo())
    mountpoints = [m['mountpoint'] for m in mounts]
    assert '/' in mountpoints


def test_iter_mountinfo_with_path():
    # Iterating children of / should yield at least the same mounts as full scan
    mounts_all = list(iter_mountinfo())
    root_mnt_id = statmount(path='/', as_dict=False).mnt_id
    mounts_rooted = list(iter_mountinfo(path='/'))
    # All rooted results must appear in the full list
    all_ids = {m['mount_id'] for m in mounts_all}
    for m in mounts_rooted:
        assert m['mount_id'] in all_ids


def test_iter_mountinfo_with_fd():
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        mounts = list(iter_mountinfo(fd=fd))
        assert len(mounts) > 0
    finally:
        os.close(fd)


def test_iter_mountinfo_with_target_mnt_id():
    root_sm = statmount(path='/', as_dict=False)
    mounts = list(iter_mountinfo(target_mnt_id=root_sm.mnt_id))
    # The root mount's children should be a subset of all mounts
    assert all(isinstance(m, dict) for m in mounts)


def test_iter_mountinfo_path_fd_mutual_exclusion():
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match='At most one'):
            list(iter_mountinfo(path='/', fd=fd))
    finally:
        os.close(fd)


def test_iter_mountinfo_path_mnt_id_mutual_exclusion():
    root_sm = statmount(path='/', as_dict=False)
    with pytest.raises(ValueError, match='At most one'):
        list(iter_mountinfo(path='/', target_mnt_id=root_sm.mnt_id))


def test_iter_mountinfo_fd_mnt_id_mutual_exclusion():
    root_sm = statmount(path='/', as_dict=False)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match='At most one'):
            list(iter_mountinfo(fd=fd, target_mnt_id=root_sm.mnt_id))
    finally:
        os.close(fd)


def test_iter_mountinfo_symlink_raises(tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(target)

    with pytest.raises(SymlinkInPathError):
        list(iter_mountinfo(path=str(link)))


def test_iter_mountinfo_reverse():
    forward = [m['mount_id'] for m in iter_mountinfo()]
    reverse = [m['mount_id'] for m in iter_mountinfo(reverse=True)]
    assert forward == list(reversed(reverse))


# ── reverse listing across the listmount() batch boundary ───────────────────────

# Must match LISTMOUNT_BATCH_SIZE in src/cext/os/mount.h; listmount()/iter_mount()
# fetch mount ids in batches of this size.
_LISTMOUNT_BATCH = 1024


def _mount_tmpfs(target):
    """Mount a fresh tmpfs at ``target`` using truenas_os' fsopen/fsmount API."""
    fs_fd = truenas_os.fsopen(fs_name='tmpfs')
    try:
        truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_CMD_CREATE)
        mnt_fd = truenas_os.fsmount(fs_fd=fs_fd)
    finally:
        os.close(fs_fd)
    try:
        truenas_os.move_mount(
            from_path='',
            to_path=target,
            from_dirfd=mnt_fd,
            flags=truenas_os.MOVE_MOUNT_F_EMPTY_PATH,
        )
    finally:
        os.close(mnt_fd)


def _build_and_check_reverse(base):
    """Create >1024 child mounts and verify reverse listing survives batching.

    Runs inside a forked child that has already unshared its mount namespace, so
    every mount created here disappears when the child exits.  Raises on the
    first failed assertion; the caller relays the message to the parent.
    """
    # Isolate propagation so none of this escapes to the host mount namespace.
    truenas_os.mount_setattr(
        path='/', propagation=truenas_os.MS_PRIVATE, flags=truenas_os.AT_RECURSIVE,
    )

    # A tmpfs is the common parent; each child is a tmpfs mounted on a directory
    # of that parent, so listmount(parent_id) enumerates exactly the children.
    _mount_tmpfs(str(base))
    parent_id = truenas_os.statx(
        str(base),
        mask=truenas_os.STATX_MNT_ID_UNIQUE | truenas_os.STATX_BASIC_STATS,
    ).stx_mnt_id

    made = 0

    def add_one():
        nonlocal made
        tgt = os.path.join(str(base), f'm{made:05d}')
        os.mkdir(tgt)
        _mount_tmpfs(tgt)
        made += 1

    # One full batch plus one forces the reverse walk to fetch a second batch.
    for _ in range(_LISTMOUNT_BATCH + 1):
        add_one()

    # The old do_listmount() only duplicated an entry when the smallest id of a
    # full reverse batch was even, so pin the continuation boundary onto an even
    # id to make that regression deterministic.  Forward listmount is unaffected
    # by the reverse bug, so it is a reliable way to read the id set.
    for _ in range(4):
        if sorted(truenas_os.listmount(parent_id))[-_LISTMOUNT_BATCH] % 2 == 0:
            break
        add_one()
    else:
        raise AssertionError('could not align the batch boundary onto an even mount id')

    ids = truenas_os.listmount(parent_id)
    n = len(ids)
    assert n > _LISTMOUNT_BATCH, f'need more than one batch, got {n} mounts'
    assert len(set(ids)) == n, 'forward listmount returned duplicate ids'
    assert ids == sorted(ids), 'forward listmount is not in ascending id order'
    assert sorted(ids)[-_LISTMOUNT_BATCH] % 2 == 0, 'batch boundary is not on an even id'
    descending = sorted(ids, reverse=True)

    # listmount(reverse=True) drives the do_listmount() continuation (mount.c).
    lm_rev = truenas_os.listmount(parent_id, reverse=True)
    assert len(lm_rev) == n, f'reverse listmount count {len(lm_rev)} != {n}'
    assert len(set(lm_rev)) == n, 'reverse listmount duplicated an id across the boundary'
    assert lm_rev == descending, 'reverse listmount is not fully descending across batches'

    # iter_mountinfo(reverse=True) drives the MountIterator continuation (iter_mount.c).
    it_fwd = [m['mount_id'] for m in iter_mountinfo(target_mnt_id=parent_id)]
    it_rev = [m['mount_id'] for m in iter_mountinfo(target_mnt_id=parent_id, reverse=True)]
    assert len(it_fwd) == n, f'iter_mountinfo forward count {len(it_fwd)} != {n}'
    assert len(it_rev) == n, f'iter_mountinfo reverse count {len(it_rev)} != {n}'
    assert len(set(it_rev)) == n, 'iter_mountinfo reverse duplicated an id across the boundary'
    assert it_rev == sorted(it_rev, reverse=True), 'iter_mountinfo reverse is not fully descending'
    assert it_fwd == list(reversed(it_rev)), 'iter_mountinfo reverse is not the reverse of forward'


@pytest.mark.skipif(os.geteuid() != 0, reason='requires root to create mounts in a private namespace')
def test_reverse_listing_across_batch_boundary(tmp_path):
    """Reverse mount listing must stay correct past the 1024-entry batch.

    Regression test for the listmount()/iter_mount() batch-continuation bug: the
    reverse flag was dropped (MountIterator) and OR'd into the cursor
    (do_listmount) after the first batch, so beyond 1024 mounts a reverse walk
    re-listed or duplicated entries.  Builds >1024 mounts in a throwaway mount
    namespace, so it needs root.  Where mounts cannot be created it skips, unless
    TRUENAS_POS_REQUIRE_PRIVILEGED is set (the CI VM does), in which case it fails
    rather than silently dropping this coverage.
    """
    base = tmp_path / 'ns_root'
    base.mkdir()

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # child: its own mount namespace, torn down when it exits
        os.close(read_fd)
        try:
            os.unshare(os.CLONE_NEWNS)
            _build_and_check_reverse(base)
            payload = b'OK'
        except OSError as exc:
            # Mounting not permitted (e.g. no CAP_SYS_ADMIN in an unprivileged
            # sandbox).  Report it and let the parent decide skip-vs-fail; the
            # reverse-listing bug surfaces as wrong data, never as one of these
            # errnos, so this branch cannot mask the regression under test.
            if exc.errno in (errno.EPERM, errno.EACCES, errno.ENOSYS):
                payload = b'NOPRIV\n' + f'cannot create mounts here: {exc}'.encode()
            else:
                import traceback
                payload = b'ERR\n' + traceback.format_exc().encode()
        except BaseException:
            import traceback
            payload = b'ERR\n' + traceback.format_exc().encode()
        try:
            os.write(write_fd, payload)
        finally:
            os._exit(0)

    os.close(write_fd)
    chunks = []
    while True:
        buf = os.read(read_fd, 65536)
        if not buf:
            break
        chunks.append(buf)
    os.close(read_fd)
    os.waitpid(pid, 0)
    result = b''.join(chunks).decode(errors='replace')
    if result.startswith('NOPRIV'):
        reason = result.split('\n', 1)[-1] or 'mount creation not permitted'
        # The CI VM runs privileged and sets TRUENAS_POS_REQUIRE_PRIVILEGED, so a
        # skip there would silently drop the only >1024-mount coverage: fail hard.
        if os.environ.get('TRUENAS_POS_REQUIRE_PRIVILEGED'):
            pytest.fail(f'privileged mount test could not run where required: {reason}')
        pytest.skip(reason)
    assert result == 'OK', result


# ── umount ────────────────────────────────────────────────────────────────────

def test_umount_nonexistent_raises():
    with pytest.raises((OSError, FileNotFoundError)):
        umount('/nonexistent_mountpoint_xyz')


def test_umount_non_mountpoint_raises(tmp_path):
    # A plain directory that is not a mountpoint
    d = str(tmp_path)
    with pytest.raises(OSError):
        umount(d)


def test_umount_recursive_non_mountpoint_raises(tmp_path):
    d = str(tmp_path)
    with pytest.raises((OSError, ValueError)):
        umount(d, recursive=True)
