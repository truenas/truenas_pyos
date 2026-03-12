from __future__ import annotations

from collections.abc import Generator
import errno
import os
from typing import Any, Literal, TypedDict, overload
import warnings

import truenas_os

from .io import SymlinkInPathError


__all__ = ["iter_mountinfo", "statmount", "umount"]

# STATMOUNT_SB_SOURCE requires kernel 6.18 or higher; the C extension
# conditionally includes the field only when the header defines it at build time.
_SB_SOURCE_SUPPORTED = hasattr(truenas_os.StatmountResult, 'sb_source')
if not _SB_SOURCE_SUPPORTED:
    warnings.warn(
        'truenas_os was built without STATMOUNT_SB_SOURCE support; '
        'mount_source will be None for all mounts and ZFS snapshot detection is disabled.',
        RuntimeWarning,
        stacklevel=1,
    )


def __parse_mnt_attr(attr: int) -> list[str]:
    out = []
    if attr & truenas_os.MOUNT_ATTR_NOATIME:
        out.append('NOATIME')

    if attr & truenas_os.MOUNT_ATTR_RELATIME:
        out.append('RELATIME')

    if attr & truenas_os.MOUNT_ATTR_NOSUID:
        out.append('NOSUID')

    if attr & truenas_os.MOUNT_ATTR_NODEV:
        out.append('NODEV')

    if attr & truenas_os.MOUNT_ATTR_NOEXEC:
        out.append('NOEXEC')

    if attr & truenas_os.MOUNT_ATTR_RDONLY:
        out.append('RO')
    else:
        out.append('RW')

    if attr & truenas_os.MOUNT_ATTR_IDMAP:
        out.append('IDMAP')

    if attr & truenas_os.MOUNT_ATTR_NOSYMFOLLOW:
        out.append('NOSYMFOLLOW')

    return out


class StatmountResultDictDeviceId(TypedDict):
    major: int | None
    minor: int | None
    dev_t: int


class StatmountResultDict(TypedDict):
    mount_id: int | None
    parent_id: int | None
    device_id: StatmountResultDictDeviceId
    root: str | None
    mountpoint: str | None
    mount_opts: list[str]
    fs_type: str | None
    mount_source: str | None
    super_opts: list[str]


def __statmount_dict(sm: truenas_os.StatmountResult) -> StatmountResultDict:
    return {
        'mount_id': sm.mnt_id,
        'parent_id': sm.mnt_parent_id,
        'device_id': {
            'major': sm.sb_dev_major,
            'minor': sm.sb_dev_minor,
            'dev_t': os.makedev(sm.sb_dev_major, sm.sb_dev_minor)  # type: ignore[arg-type]
        },
        'root': sm.mnt_root,
        'mountpoint': sm.mnt_point,
        'mount_opts': __parse_mnt_attr(sm.mnt_attr),  # type: ignore[arg-type]
        'fs_type': sm.fs_type,
        'mount_source': sm.sb_source if _SB_SOURCE_SUPPORTED else None,
        'super_opts': sm.mnt_opts.upper().split(',') if sm.mnt_opts else []
    }


def _is_zfs_snapshot_mount(sm: truenas_os.StatmountResult) -> bool:
    """
    Return True if this mount is a ZFS snapshot.

    Two conditions must both hold:
    - ``fs_type`` is ``"zfs"``
    - ``sb_source`` contains ``"@"`` (the ZFS snapshot name separator)

    ``sb_source`` comes from ``statmount(2)`` via ZFS's ``show_devname``
    superblock operation (``zpl_show_devname`` → ``dmu_objset_name``), which
    returns the full dataset name.  Regular datasets yield ``pool/dataset``;
    snapshots yield ``pool/dataset@snapname``.  The ``fs_type`` guard prevents
    a false match on non-ZFS filesystems that happen to include ``@`` in their
    source name.
    """
    return _SB_SOURCE_SUPPORTED and sm.fs_type == 'zfs' and sm.sb_source is not None and '@' in sm.sb_source


@overload
def iter_mountinfo(
    *,
    target_mnt_id: int | None = None,
    path: str | bytes | None = None,
    fd: int | None = None,
    reverse: bool = False,
    as_dict: Literal[True] = True,
    include_snapshot_mounts: bool = False,
) -> Generator[StatmountResultDict]: ...


@overload
def iter_mountinfo(
    *,
    target_mnt_id: int | None = None,
    path: str | bytes | None = None,
    fd: int | None = None,
    reverse: bool = False,
    as_dict: Literal[False],
    include_snapshot_mounts: bool = False,
) -> Generator[truenas_os.StatmountResult]: ...


def iter_mountinfo(
    *,
    target_mnt_id: int | None = None,
    path: str | bytes | None = None,
    fd: int | None = None,
    reverse: bool = False,
    as_dict: bool = True,
    include_snapshot_mounts: bool = False,
) -> Generator[StatmountResultDict] | Generator[truenas_os.StatmountResult]:
    """Iterate mountpoints on the system.

    Args:
        target_mnt_id: Restrict iteration to children of this mount ID.
                       Mutually exclusive with path and fd.
        path: Restrict iteration to children of the mount containing this path.
              Mutually exclusive with fd and target_mnt_id.
        fd: Restrict iteration to children of the mount containing this open
            file descriptor. Mutually exclusive with path and target_mnt_id.
        reverse: If True, yield mounts in reverse order. Useful for unmount
                 operations where children must be processed before parents
                 (default: False).
        as_dict: If True, yield StatmountResultDict dictionaries. If False,
                 yield raw truenas_os.StatmountResult objects (default: True).
        include_snapshot_mounts: If True, include ZFS snapshot mounts in
                                 results (default: False).

    Yields:
        StatmountResultDict if as_dict is True, otherwise StatmountResult.

    Raises:
        ValueError: If more than one of target_mnt_id, path, and fd is specified.
        SymlinkInPathError: If a symlink is detected in path.
        OSError: For other path resolution or statmount failures.

    Note:
        - At most one of target_mnt_id, path, and fd may be specified. If none
          are given, all mounts on the system are iterated.
        - ZFS snapshot mounts (fs_type == "zfs" and "@" in sb_source) are
          excluded by default because they are transient: ZFS mounts them on
          first access of .zfs/snapshot/<name> and expires them after
          zfs_expire_snapshot seconds (default 300 s). Pass
          include_snapshot_mounts=True to include them — needed when enumerating
          all child mounts for recursive unmount operations.
    """
    specifiers = sum(x is not None for x in (target_mnt_id, path, fd))
    if specifiers > 1:
        raise ValueError('At most one of target_mnt_id, path, and fd may be specified')

    if path is not None or fd is not None:
        target_mnt_id = statmount(path=path, fd=fd, as_dict=False).mnt_id

    iter_kwargs: dict[str, Any] = {'reverse': reverse, 'statmount_flags': truenas_os.STATMOUNT_ALL}
    if target_mnt_id:
        iter_kwargs['mnt_id'] = target_mnt_id

    for sm in truenas_os.iter_mount(**iter_kwargs):
        if not include_snapshot_mounts and _is_zfs_snapshot_mount(sm):
            continue
        if as_dict:
            yield __statmount_dict(sm)
        else:
            yield sm


@overload
def statmount(
    *,
    path: str | bytes | None = None,
    fd: int | None = None,
    as_dict: Literal[True] = True
) -> StatmountResultDict: ...


@overload
def statmount(
    *,
    path: str | bytes | None = None,
    fd: int | None = None,
    as_dict: Literal[False]
) -> truenas_os.StatmountResult: ...


def statmount(
    *,
    path: str | bytes | None = None,
    fd: int | None = None,
    as_dict: bool = True
) -> truenas_os.StatmountResult | StatmountResultDict:
    """Get mount information for the filesystem containing a path or open file.

    Args:
        path: Path whose containing mount is queried. Mutually exclusive with fd.
        fd: Open file descriptor whose containing mount is queried.
            Mutually exclusive with path.
        as_dict: If True, return a StatmountResultDict. If False, return a raw
                 truenas_os.StatmountResult object (default: True).

    Returns:
        StatmountResultDict if as_dict is True, otherwise StatmountResult.

    Raises:
        ValueError: If neither or both of path and fd are specified.
        SymlinkInPathError: If a symlink is detected in path.
        FileNotFoundError: If path does not exist.
        OSError: For other path resolution or statmount failures.

    Note:
        - When path is given, openat2(RESOLVE_NO_SYMLINKS) is used to open it
          before calling statx, so symlinks anywhere in the path are rejected.
        - When fd is given, statx is called with AT_EMPTY_PATH to resolve the
          mount ID without re-opening the file.
    """
    if (not path and not fd) or (path and fd):
        raise ValueError('One of path or fd is required')

    if path:
        try:
            opened_fd = truenas_os.openat2(path, os.O_PATH, resolve=truenas_os.RESOLVE_NO_SYMLINKS)
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise SymlinkInPathError(os.fsdecode(path)) from e
            raise
        try:
            mnt_id = truenas_os.statx(
                '', dir_fd=opened_fd, flags=truenas_os.AT_EMPTY_PATH, mask=truenas_os.STATX_MNT_ID_UNIQUE
            ).stx_mnt_id
        finally:
            os.close(opened_fd)
    else:
        mnt_id = truenas_os.statx(
            '', dir_fd=fd, flags=truenas_os.AT_EMPTY_PATH, mask=truenas_os.STATX_MNT_ID_UNIQUE  # type: ignore[arg-type]
        ).stx_mnt_id

    sm = truenas_os.statmount(mnt_id, mask=truenas_os.STATMOUNT_ALL)
    if not as_dict:
        return sm

    return __statmount_dict(sm)


def umount(
    path: str,
    *,
    force: bool = False,
    detach: bool = False,
    expire: bool = False,
    follow_symlinks: bool = False,
    recursive: bool = False
) -> None:
    """Unmount the filesystem at the given path.

    Args:
        path: Path to the mountpoint to unmount.
        force: If True, force unmount even if busy (MNT_FORCE). Note that
               MNT_FORCE is a no-op on most filesystems including ZFS
               (default: False).
        detach: If True, detach the mount from the filesystem hierarchy
                immediately and clean up when no longer busy (MNT_DETACH)
                (default: False).
        expire: If True, mark the mount as expired; a second call with expire
                will unmount it if it has not been accessed since the first
                call (MNT_EXPIRE) (default: False).
        follow_symlinks: If True, follow symlinks in path. If False, reject
                         symlinks via UMOUNT_NOFOLLOW (default: False).
        recursive: If True, recursively unmount all child mounts before
                   unmounting the target (default: False).

    Raises:
        ValueError: If path is not a mountpoint and recursive is True.
        FileNotFoundError: If path does not exist.
        OSError: See umount2(2) manpage for errno explanations.

    Note:
        - recursive uses iter_mountinfo with include_snapshot_mounts=True so
          that transiently-triggered ZFS snapshot mounts are also cleaned up.
    """
    # Build flags from boolean arguments
    flags = 0
    if force:
        flags |= truenas_os.MNT_FORCE
    if detach:
        flags |= truenas_os.MNT_DETACH
    if expire:
        flags |= truenas_os.MNT_EXPIRE
    if not follow_symlinks:
        flags |= truenas_os.UMOUNT_NOFOLLOW

    if recursive:
        # Get the mount ID of the target path and verify it's a mountpoint
        stat_result = truenas_os.statx(path, mask=truenas_os.STATX_MNT_ID_UNIQUE | truenas_os.STATX_BASIC_STATS)
        if not (stat_result.stx_attributes & truenas_os.STATX_ATTR_MOUNT_ROOT):
            raise ValueError(f'{path!r} is not a mountpoint')

        mnt_id = stat_result.stx_mnt_id

        # Unmount all child mounts first, including any triggered snapshot mounts
        for mnt in iter_mountinfo(target_mnt_id=mnt_id, reverse=True, include_snapshot_mounts=True):
            assert mnt['mountpoint'] is not None
            truenas_os.umount2(target=mnt['mountpoint'], flags=flags)

    # Unmount the target path itself
    truenas_os.umount2(target=path, flags=flags)
