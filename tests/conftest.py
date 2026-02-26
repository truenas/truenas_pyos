# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Pytest fixtures for truenas_os ACL tests.

Session fixtures create a single ZFS test pool backed by a sparse
200 MiB file-based vdev.  Per-test fixtures carve out individual datasets
with the correct acltype.  When ZFS or root access is unavailable, NFS4
tests are skipped and POSIX tests fall back to a plain tmpdir (POSIX ACLs
work on most Linux filesystems).
"""

import gc
import os
import shutil
import subprocess
import tempfile
import time

import pytest


# ── ZFS availability helpers ─────────────────────────────────────────────────

def _zfs_available():
    try:
        r = subprocess.run(['zfs', 'version'], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── Session-scoped: pool ─────────────────────────────────────────────────────

@pytest.fixture(scope='session')
def zfs_enabled():
    """True when ZFS commands are present and the process is running as root."""
    return _zfs_available() and os.geteuid() == 0


@pytest.fixture(scope='session')
def zpool(zfs_enabled):
    """
    Create a single ZFS test pool for the whole test session using a
    sparse 200 MiB file-backed vdev.  Yields the pool name.

    When ZFS is unavailable the fixture yields None so that per-test
    fixtures can fall back gracefully rather than raising.
    """
    if not zfs_enabled:
        yield None
        return

    tmpdir    = tempfile.mkdtemp(prefix='truenas_pos_vdev_')
    vdev_file = os.path.join(tmpdir, 'vdev.img')
    altroot   = tempfile.mkdtemp(prefix='truenas_pos_altroot_')
    pool_name = f'postest_{os.getpid()}'

    try:
        subprocess.run(['truncate', '-s', '200M', vdev_file], check=True)
        subprocess.run(
            ['zpool', 'create', pool_name, vdev_file, '-R', altroot],
            check=True,
        )
        yield pool_name
    finally:
        subprocess.run(
            ['zpool', 'destroy', '-f', pool_name],
            timeout=30, check=False,
        )
        shutil.rmtree(tmpdir,  ignore_errors=True)
        shutil.rmtree(altroot, ignore_errors=True)


# ── Dataset helpers ───────────────────────────────────────────────────────────

_ds_counter = 0


def _zfs_create_dataset(pool, acltype):
    """
    Create a ZFS dataset with the specified acltype.
    Uses legacy mountpoint and mounts manually so the path is known
    regardless of pool altroot.
    Returns (dataset_name, mountpoint).
    """
    global _ds_counter
    _ds_counter += 1

    ds = f'{pool}/acl_{acltype}_{_ds_counter}'
    mountpoint = tempfile.mkdtemp(prefix=f'truenas_pos_{acltype}_')

    subprocess.run(['zfs', 'create', ds], check=True)
    subprocess.run(['zfs', 'set', 'mountpoint=legacy', ds], check=True)
    subprocess.run(['zfs', 'set', f'acltype={acltype}', ds], check=True)
    if acltype == 'nfsv4':
        subprocess.run(['zfs', 'set', 'aclmode=passthrough', ds], check=True)
        subprocess.run(['zfs', 'set', 'aclinherit=passthrough', ds], check=True)

    subprocess.run(['mount', '-t', 'zfs', ds, mountpoint], check=True)
    return ds, mountpoint


def _zfs_destroy_dataset(ds, mountpoint):
    """
    Unmount and destroy a ZFS dataset, retrying for up to 30 seconds on EBUSY.
    Two rounds of gc.collect() are run first to release any open fds
    held by Python objects that have not yet been finalized.
    """
    gc.collect()
    gc.collect()

    subprocess.run(['umount', mountpoint], check=False)
    shutil.rmtree(mountpoint, ignore_errors=True)

    for attempt in range(30):
        try:
            subprocess.run(
                ['zfs', 'destroy', '-f', ds],
                capture_output=True, text=True, check=True, timeout=30,
            )
            return
        except subprocess.CalledProcessError as exc:
            if 'busy' in exc.stderr.lower() and attempt < 29:
                gc.collect()
                time.sleep(1)
            else:
                raise


# ── Per-test dataset fixtures ─────────────────────────────────────────────────

@pytest.fixture(scope='function')
def nfs4_dataset(zpool, zfs_enabled):
    """
    ZFS dataset with acltype=nfsv4 when ZFS is available; otherwise
    assumes /NFS4ACL exists and has acltype=nfsv4.
    Yields the mountpoint path.
    """
    if zfs_enabled and zpool is not None:
        ds, mountpoint = _zfs_create_dataset(zpool, 'nfsv4')
        yield mountpoint
        _zfs_destroy_dataset(ds, mountpoint)
    else:
        yield '/NFSV4ACL'


@pytest.fixture(scope='function')
def posix_dataset(zpool, zfs_enabled):
    """
    ZFS dataset with acltype=posixacl when ZFS is available; otherwise
    assumes /POSIXACL exists and has acltype=posixacl.
    Yields the mountpoint path.
    """
    if zfs_enabled and zpool is not None:
        ds, mountpoint = _zfs_create_dataset(zpool, 'posixacl')
        yield mountpoint
        _zfs_destroy_dataset(ds, mountpoint)
    else:
        yield '/POSIXACL'
