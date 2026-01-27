"""Type stubs for truenas_os module.

This module provides Python bindings to Linux kernel system calls for
advanced filesystem and mount operations.
"""

from typing import Any, Callable, Iterator, NamedTuple

# StatxResult type - PyStructSequence from statx(2)
class StatxResult(NamedTuple):
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
class StatmountResult(NamedTuple):
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

# mount_setattr function
def mount_setattr(
    *,
    path: str,
    attr_set: int = 0,
    attr_clr: int = 0,
    propagation: int = 0,
    userns_fd: int = 0,
    dirfd: int = ...,  # Default: AT_FDCWD
    flags: int = 0,
) -> None:
    """Change properties of a mount or mount tree.

    The mount_setattr() system call changes the mount properties of a mount
    or an entire mount tree. If path is a relative pathname, then it is
    interpreted relative to the directory referred to by dirfd.

    If flags includes AT_RECURSIVE, all mounts in the subtree are affected.

    Parameters
    ----------
    path : str
        Path to the mount point (can be relative to dirfd)
    attr_set : int, optional
        Mount attributes to set (MOUNT_ATTR_* constants)
    attr_clr : int, optional
        Mount attributes to clear (MOUNT_ATTR_* constants)
    propagation : int, optional
        Mount propagation type (MS_SHARED, MS_SLAVE, MS_PRIVATE, MS_UNBINDABLE)
    userns_fd : int, optional
        User namespace file descriptor for MOUNT_ATTR_IDMAP
    dirfd : int, optional
        Directory file descriptor
    flags : int, optional
        Flags (AT_EMPTY_PATH, AT_RECURSIVE, AT_SYMLINK_NOFOLLOW, etc.)
    """
    ...

# fsopen function
def fsopen(
    *,
    fs_name: str,
    flags: int = 0,
) -> int:
    """Open a filesystem context for configuration.

    The fsopen() system call creates a blank filesystem configuration context
    for the filesystem type specified by fs_name. This context can then be
    configured using fsconfig() before creating a mount with fsmount().

    Parameters
    ----------
    fs_name : str
        Filesystem type name (e.g., 'ext4', 'xfs', 'tmpfs')
    flags : int, optional
        Flags controlling behavior (FSOPEN_* constants)

    Returns
    -------
    int
        File descriptor for the filesystem context
    """
    ...

# fsconfig function
def fsconfig(
    *,
    fs_fd: int,
    cmd: int,
    key: str | None = None,
    value: str | bytes | None = None,
    aux: int = 0,
) -> None:
    """Configure a filesystem context.

    The fsconfig() system call is used to configure a filesystem context
    created by fsopen(). It can set options, provide a source device, and
    trigger filesystem creation or reconfiguration.

    Parameters
    ----------
    fs_fd : int
        File descriptor from fsopen()
    cmd : int
        Configuration command (FSCONFIG_* constants)
    key : str | None, optional
        Option name (for SET_FLAG, SET_STRING, SET_PATH, etc.)
    value : str | bytes | None, optional
        Option value (for SET_STRING, SET_BINARY, SET_PATH, etc.)
    aux : int, optional
        Auxiliary parameter (for SET_FD)

    FSCONFIG_* commands
    -------------------
    FSCONFIG_SET_FLAG : Set a flag option (key only, no value)
    FSCONFIG_SET_STRING : Set a string-valued option
    FSCONFIG_SET_BINARY : Set a binary blob option
    FSCONFIG_SET_PATH : Set an option from a file path
    FSCONFIG_SET_PATH_EMPTY : Set from an empty path
    FSCONFIG_SET_FD : Set from a file descriptor
    FSCONFIG_CMD_CREATE : Create the filesystem
    FSCONFIG_CMD_RECONFIGURE : Reconfigure the filesystem
    """
    ...

# fsmount function
def fsmount(
    *,
    fs_fd: int,
    flags: int = 0,
    attr_flags: int = 0,
) -> int:
    """Create a mount object from a configured filesystem context.

    The fsmount() system call takes a filesystem context created by fsopen()
    and configured with fsconfig(), and creates a mount object. This mount
    can then be attached to the filesystem tree using move_mount().

    Parameters
    ----------
    fs_fd : int
        File descriptor from fsopen() (after configuration with fsconfig())
    flags : int, optional
        Mount flags (FSMOUNT_* constants)
    attr_flags : int, optional
        Mount attribute flags (MOUNT_ATTR_* constants)

    Returns
    -------
    int
        File descriptor for the mount object
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
AT_RECURSIVE: int  # Apply to entire subtree (for mount_setattr)

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

# FSOPEN constants (for fsopen)
FSOPEN_CLOEXEC: int  # Close-on-exec flag

# FSCONFIG constants (for fsconfig commands)
FSCONFIG_SET_FLAG: int  # Set parameter, supplying no value
FSCONFIG_SET_STRING: int  # Set parameter, supplying a string value
FSCONFIG_SET_BINARY: int  # Set parameter, supplying a binary blob value
FSCONFIG_SET_PATH: int  # Set parameter, supplying an object by path
FSCONFIG_SET_PATH_EMPTY: int  # Set parameter, supplying an object by (empty) path
FSCONFIG_SET_FD: int  # Set parameter, supplying an object by fd
FSCONFIG_CMD_CREATE: int  # Invoke superblock creation
FSCONFIG_CMD_RECONFIGURE: int  # Invoke superblock reconfiguration

# FSMOUNT constants (for fsmount)
FSMOUNT_CLOEXEC: int  # Close-on-exec flag

# fhandle type
class fhandle:
    """File handle object for name_to_handle_at and open_by_handle_at operations."""
    def __init__(self) -> None: ...

# Filesystem iterator types
class IterInstance(NamedTuple):
    """Instance returned by filesystem iterator.

    Represents a file or directory encountered during iteration.
    The file descriptor must be closed by the caller.
    """
    parent: str  # Parent directory path
    name: str  # Entry name
    fd: int  # Open file descriptor
    statxinfo: StatxResult  # Extended file attributes
    isdir: bool  # True if directory, False if file

class FilesystemIterState(NamedTuple):
    """State for filesystem iteration.

    Tracks iteration progress and configuration.
    """
    cnt: int  # Count of items yielded
    cnt_bytes: int  # Total bytes of files yielded
    current_directory: str  # Current directory path

class FilesystemIterator:
    """Iterator for traversing filesystem contents in C.

    Internal iterator object created by iter_filesystem_contents.
    Implements depth-first traversal with GIL released during I/O.
    """
    def __iter__(self) -> FilesystemIterator: ...
    def __next__(self) -> IterInstance: ...
    def get_stats(self) -> FilesystemIterState:
        """Return current iteration statistics.

        Returns a FilesystemIterState object with current count, bytes, and configuration.
        """
        ...
    def skip(self) -> None:
        """Skip recursion into the currently yielded directory.

        Must be called immediately after the iterator yields a directory,
        before calling next() again. Prevents the iterator from recursing
        into the directory that was just yielded.

        Raises:
            ValueError: If the last yielded item was not a directory.
        """
        ...
    def dir_stack(self) -> tuple[tuple[str, int], ...]:
        """Return the current directory stack as a tuple of (path, inode) tuples.

        Returns a tuple of tuples where each tuple contains:
          - path (str): The full directory path
          - inode (int): The inode number of the directory

        The first element is the root directory, and the last element is the
        current directory being processed.

        Returns an empty tuple if iteration has completed.
        """
        ...

def iter_filesystem_contents(
    mountpoint: str,
    filesystem_name: str,
    /,
    *,
    relative_path: str | None = None,
    btime_cutoff: int = 0,
    cnt: int = 0,
    cnt_bytes: int = 0,
    file_open_flags: int = ...,
    reporting_increment: int = 1000,
    reporting_callback: Callable[[tuple[tuple[str, int], ...], FilesystemIterState, Any], Any] | None = None,
    reporting_private_data: Any = None,
) -> FilesystemIterator:
    """Iterate filesystem contents with mount validation.

    Opens and validates a filesystem path, then returns an iterator that
    yields IterInstance objects for each file and directory.

    Args:
        mountpoint: Expected mount point (e.g., "/mnt/tank/dataset")
        filesystem_name: Filesystem source name (e.g., "tank/dataset")
            Must match sb_source from statmount
        relative_path: Optional path relative to mountpoint
        btime_cutoff: Skip files with birth time > cutoff (seconds since epoch, 0=disabled)
        cnt: Initial count of items yielded
        cnt_bytes: Initial count of bytes yielded
        file_open_flags: Flags for opening files (default: O_RDONLY | O_NOFOLLOW)
        reporting_increment: Call reporting_callback every N items (0 to disable)
        reporting_callback: Callback function(dir_stack, state, private_data) called every
            reporting_increment items with current iteration state and directory stack
        reporting_private_data: User data passed to reporting_callback

    Returns:
        FilesystemIterator that yields IterInstance objects

    Raises:
        OSError: Filesystem errors (open, statx, statmount failures)
        RuntimeError: Mount validation failures
        NotADirectoryError: Path is not a directory
        TypeError: reporting_callback is not callable

    Notes:
        - Uses openat2 with RESOLVE_NO_XDEV | RESOLVE_NO_SYMLINKS
        - Uses statx for extended attributes including birth time
        - Directories with matching resume tokens are skipped
        - GIL is released during I/O operations
        - File descriptors in IterInstance must be closed by caller
        - Reporting callback is invoked with GIL held after every Nth item
        - If callback raises an exception, iteration stops
    """
    ...
