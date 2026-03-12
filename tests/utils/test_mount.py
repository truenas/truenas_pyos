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
