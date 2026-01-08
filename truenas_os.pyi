"""Type stubs for truenas_os module.

This module provides Python bindings to Linux kernel system calls for
advanced filesystem and mount operations.
"""

from typing import Iterator

# StatxResult type - PyStructSequence from statx(2)
class StatxResult:
    """Extended file attributes from statx(2) system call.

    This is a named tuple-like structure containing file metadata.
    Fields not requested or unavailable may be 0 or unset.
    """
    stx_mask: int
    stx_blksize: int
    stx_attributes: int
    stx_nlink: int
    stx_uid: int
    stx_gid: int
    stx_mode: int
    stx_ino: int
    stx_size: int
    stx_blocks: int
    stx_attributes_mask: int
    stx_atime: float  # Seconds since epoch with fractional part
    stx_atime_ns: int  # Nanoseconds since epoch
    stx_btime: float  # Birth/creation time
    stx_btime_ns: int
    stx_ctime: float  # Status change time
    stx_ctime_ns: int
    stx_mtime: float  # Modification time
    stx_mtime_ns: int
    stx_rdev_major: int
    stx_rdev_minor: int
    stx_rdev: int
    stx_dev_major: int
    stx_dev_minor: int
    stx_dev: int
    stx_mnt_id: int
    stx_dio_mem_align: int
    stx_dio_offset_align: int
    stx_subvol: int
    stx_atomic_write_unit_min: int
    stx_atomic_write_unit_max: int
    stx_atomic_write_segments_max: int
    # Optional fields (kernel version dependent):
    # stx_dio_read_offset_align: int
    # stx_atomic_write_unit_max_opt: int

# StatmountResult type - PyStructSequence from statmount(2)
class StatmountResult:
    """Mount point information from statmount(2) system call.

    This is a named tuple-like structure containing mount metadata.
    Fields not requested will be None.
    """
    mnt_id: int | None
    mnt_parent_id: int | None
    mnt_id_old: int | None
    mnt_parent_id_old: int | None
    mnt_root: str | None
    mnt_point: str | None
    mnt_attr: int | None
    mnt_propagation: int | None
    mnt_peer_group: int | None
    mnt_master: int | None
    propagate_from: int | None
    fs_type: str | None
    mnt_ns_id: int | None
    mnt_opts: str | None
    sb_dev_major: int | None
    sb_dev_minor: int | None
    sb_magic: int | None
    sb_flags: int | None
    fs_subtype: str | None  # Optional field
    sb_source: str | None  # Optional field
    opt_array: list[str] | None  # Optional field
    opt_sec_array: list[str] | None  # Optional field
    supported_mask: int | None  # Optional field
    mnt_uidmap: str | None  # Optional field
    mnt_gidmap: str | None  # Optional field
    mask: int

# statx function
def statx(
    path: str | bytes,
    *,
    dir_fd: int = ...,  # Default: AT_FDCWD
    flags: int = 0,
    mask: int = ...,  # Default: STATX_BASIC_STATS | STATX_BTIME
) -> StatxResult:
    """Get extended file attributes.

    Parameters
    ----------
    path : str | bytes
        Path to the file (relative to dir_fd)
    dir_fd : int, optional
        Directory file descriptor (default: AT_FDCWD)
    flags : int, optional
        Flags controlling behavior (AT_* constants)
    mask : int, optional
        Mask of fields to retrieve (STATX_* constants)

    Returns
    -------
    StatxResult
        Named tuple with extended file attributes
    """
    ...

# statmount function
def statmount(
    mnt_id: int,
    *,
    mask: int = ...,  # Default: STATMOUNT_MNT_BASIC | STATMOUNT_SB_BASIC
) -> StatmountResult:
    """Get detailed information about a mount.

    Parameters
    ----------
    mnt_id : int
        Mount ID to query
    mask : int, optional
        Mask of fields to retrieve (STATMOUNT_* constants)

    Returns
    -------
    StatmountResult
        Named tuple with mount information
    """
    ...

# listmount function
def listmount(
    *,
    mnt_id: int | None = None,
) -> list[int]:
    """List mount IDs.

    Parameters
    ----------
    mnt_id : int | None, optional
        Mount ID to list children of (None for all mounts)

    Returns
    -------
    list[int]
        List of mount IDs
    """
    ...

# iter_mount function
def iter_mount(
    *,
    mnt_id: int | None = None,
    reverse: bool = False,
    statmount_flags: int = ...,  # Default: STATMOUNT_MNT_BASIC | STATMOUNT_SB_BASIC
) -> Iterator[StatmountResult]:
    """Create an iterator over mount information.

    Parameters
    ----------
    mnt_id : int | None, optional
        Mount ID to list children of (None for root)
    reverse : bool, optional
        List mounts in reverse order
    statmount_flags : int, optional
        Mask of fields to retrieve for each mount

    Returns
    -------
    Iterator[StatmountResult]
        Iterator yielding StatmountResult objects
    """
    ...

# openat2 function
def openat2(
    path: str | bytes,
    *,
    dir_fd: int = ...,  # Default: AT_FDCWD
    flags: int = 0,
    mode: int = 0,
    resolve: int = 0,
) -> int:
    """Extended openat with path resolution control.

    Parameters
    ----------
    path : str | bytes
        Path to the file
    dir_fd : int, optional
        Directory file descriptor
    flags : int, optional
        File creation and status flags (O_* constants)
    mode : int, optional
        File mode for new files
    resolve : int, optional
        Path resolution flags (RESOLVE_* constants)

    Returns
    -------
    int
        File descriptor
    """
    ...

# open_mount_by_id function
def open_mount_by_id(
    mnt_id: int,
    *,
    flags: int = 0,
) -> int:
    """Open a mount by its mount ID.

    Parameters
    ----------
    mnt_id : int
        Mount ID to open
    flags : int, optional
        Open flags

    Returns
    -------
    int
        File descriptor
    """
    ...

# move_mount function
def move_mount(
    *,
    from_path: str,
    to_path: str,
    from_dirfd: int = ...,  # Default: AT_FDCWD
    to_dirfd: int = ...,  # Default: AT_FDCWD
    flags: int = 0,
) -> None:
    """Move a mount from one location to another.

    Parameters
    ----------
    from_path : str
        Source path
    to_path : str
        Destination path
    from_dirfd : int, optional
        Source directory file descriptor
    to_dirfd : int, optional
        Destination directory file descriptor
    flags : int, optional
        Move flags (MOVE_MOUNT_* constants)
    """
    ...

# STATX constants
STATX_TYPE: int
STATX_MODE: int
STATX_NLINK: int
STATX_UID: int
STATX_GID: int
STATX_ATIME: int
STATX_MTIME: int
STATX_CTIME: int
STATX_INO: int
STATX_SIZE: int
STATX_BLOCKS: int
STATX_BASIC_STATS: int
STATX_BTIME: int
STATX_MNT_ID: int
STATX_DIOALIGN: int
STATX_MNT_ID_UNIQUE: int
STATX_SUBVOL: int
STATX_WRITE_ATOMIC: int
STATX_DIO_READ_ALIGN: int  # May not be available on all kernels
STATX__RESERVED: int
STATX_ALL: int

# AT constants
AT_FDCWD: int
AT_SYMLINK_NOFOLLOW: int
AT_REMOVEDIR: int
AT_SYMLINK_FOLLOW: int
AT_NO_AUTOMOUNT: int
AT_EMPTY_PATH: int
AT_STATX_SYNC_AS_STAT: int
AT_STATX_FORCE_SYNC: int
AT_STATX_DONT_SYNC: int

# STATX_ATTR constants
STATX_ATTR_COMPRESSED: int
STATX_ATTR_IMMUTABLE: int
STATX_ATTR_APPEND: int
STATX_ATTR_NODUMP: int
STATX_ATTR_ENCRYPTED: int
STATX_ATTR_AUTOMOUNT: int
STATX_ATTR_MOUNT_ROOT: int
STATX_ATTR_VERITY: int
STATX_ATTR_DAX: int
STATX_ATTR_WRITE_ATOMIC: int

# MOUNT_ATTR constants
MOUNT_ATTR_RDONLY: int
MOUNT_ATTR_NOSUID: int
MOUNT_ATTR_NODEV: int
MOUNT_ATTR_NOEXEC: int
MOUNT_ATTR__ATIME: int
MOUNT_ATTR_RELATIME: int
MOUNT_ATTR_NOATIME: int
MOUNT_ATTR_STRICTATIME: int
MOUNT_ATTR_NODIRATIME: int
MOUNT_ATTR_IDMAP: int
MOUNT_ATTR_NOSYMFOLLOW: int

# STATMOUNT constants
STATMOUNT_SB_BASIC: int
STATMOUNT_MNT_BASIC: int
STATMOUNT_PROPAGATE_FROM: int
STATMOUNT_MNT_ROOT: int
STATMOUNT_MNT_POINT: int
STATMOUNT_FS_TYPE: int
STATMOUNT_FS_SUBTYPE: int
STATMOUNT_MNT_NS_ID: int
STATMOUNT_MNT_OPTS: int
STATMOUNT_SB_SOURCE: int
STATMOUNT_OPT_ARRAY: int
STATMOUNT_OPT_SEC_ARRAY: int
STATMOUNT_MNT_UIDMAP: int
STATMOUNT_MNT_GIDMAP: int
STATMOUNT_SUPPORTED_MASK: int
STATMOUNT_ALL: int

# MOVE_MOUNT constants
MOVE_MOUNT_F_SYMLINKS: int
MOVE_MOUNT_F_AUTOMOUNTS: int
MOVE_MOUNT_F_EMPTY_PATH: int
MOVE_MOUNT_T_SYMLINKS: int
MOVE_MOUNT_T_AUTOMOUNTS: int
MOVE_MOUNT_T_EMPTY_PATH: int
MOVE_MOUNT_SET_GROUP: int
MOVE_MOUNT_BENEATH: int

# RESOLVE constants (for openat2)
RESOLVE_NO_XDEV: int
RESOLVE_NO_MAGICLINKS: int
RESOLVE_NO_SYMLINKS: int
RESOLVE_BENEATH: int
RESOLVE_IN_ROOT: int
RESOLVE_CACHED: int

# FH constants (file handle)
FH_AT_SYMLINK_FOLLOW: int
FH_AT_EMPTY_PATH: int
FH_AT_HANDLE_FID: int
FH_AT_HANDLE_CONNECTABLE: int

# fhandle type
class fhandle:
    """File handle object for name_to_handle_at and open_by_handle_at operations."""
    def __init__(self) -> None: ...
