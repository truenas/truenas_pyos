"""Type stubs for truenas_os module.

This module provides Python bindings to Linux kernel system calls for
advanced filesystem and mount operations, plus ACL support.
"""

from typing import Any, Callable, Iterable, Iterator, NamedTuple
from enum import IntEnum, IntFlag

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
    dir_fd: int = ...,  # Default: AT_FDCWD
    *,
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

# umount2 function
def umount2(
    *,
    target: str,
    flags: int = 0,
) -> None:
    """Unmount a filesystem.

    The umount2() system call unmounts the filesystem mounted at the specified
    target. The flags parameter controls the unmount behavior, allowing for
    forced unmounts, lazy unmounts, or expiration of mount points.

    Parameters
    ----------
    target : str
        Path to the mount point to unmount
    flags : int, optional
        Unmount flags (MNT_* and UMOUNT_* constants)

    MNT_* and UMOUNT_* flags
    ------------------------
    MNT_FORCE : Force unmount even if busy (may cause data loss)
    MNT_DETACH : Lazy unmount - detach filesystem from hierarchy now,
                 clean up references when no longer busy
    MNT_EXPIRE : Mark mount point as expired. If not busy, unmount it.
                 Repeated calls will unmount an expired mount.
    UMOUNT_NOFOLLOW : Don't dereference target if it is a symbolic link
    """
    ...

# renameat2 function
def renameat2(
    src: str | bytes,
    dst: str | bytes,
    *,
    src_dir_fd: int = ...,  # Default: AT_FDCWD
    dst_dir_fd: int = ...,  # Default: AT_FDCWD
    flags: int = 0,
) -> None:
    """Rename a file with additional flags.

    The renameat2() system call provides extended rename functionality with
    additional flags for atomic operations. It can perform normal renames,
    exchange two files atomically, or ensure the destination doesn't exist.

    Parameters
    ----------
    src : str | bytes
        Source path (relative to src_dir_fd)
    dst : str | bytes
        Destination path (relative to dst_dir_fd)
    src_dir_fd : int, optional
        Source directory file descriptor (default: AT_FDCWD)
    dst_dir_fd : int, optional
        Destination directory file descriptor (default: AT_FDCWD)
    flags : int, optional
        Rename flags (AT_RENAME_* constants)

    AT_RENAME_* flags
    -----------------
    AT_RENAME_NOREPLACE : Don't overwrite newpath if it exists (fails with EEXIST)
    AT_RENAME_EXCHANGE : Atomically exchange oldpath and newpath (both must exist)
    AT_RENAME_WHITEOUT : Create a whiteout object at oldpath after rename

    Notes
    -----
    - AT_RENAME_NOREPLACE and AT_RENAME_EXCHANGE are mutually exclusive
    - With AT_RENAME_EXCHANGE, both paths must exist or ENOENT is raised
    - The operation is atomic - either completes fully or fails completely
    - Inode numbers are preserved during rename operations
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

# AT_RENAME constants (for renameat2)
AT_RENAME_NOREPLACE: int  # Don't overwrite newpath if it exists
AT_RENAME_EXCHANGE: int  # Atomically exchange oldpath and newpath
AT_RENAME_WHITEOUT: int  # Create whiteout object at oldpath

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

# umount2 constants
MNT_FORCE: int  # Force unmount even if busy (may cause data loss)
MNT_DETACH: int  # Lazy unmount - detach now, clean up when not busy
MNT_EXPIRE: int  # Mark mount point as expired
UMOUNT_NOFOLLOW: int  # Don't dereference target if symbolic link

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
    isdir: bool  # True if directory, False otherwise
    islnk: bool  # True if symlink, False otherwise
    isreg: bool  # True if regular file, False otherwise

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
    Supports context manager protocol for automatic resource cleanup.
    """
    def __iter__(self) -> FilesystemIterator: ...
    def __next__(self) -> IterInstance: ...
    def __enter__(self) -> FilesystemIterator:
        """Enter context manager.

        Returns:
            FilesystemIterator: The iterator itself.

        Raises:
            ValueError: If the iterator has already been closed.
        """
        ...
    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any) -> bool:
        """Exit context manager, closing the iterator.

        Closes all open file descriptors and releases resources.

        Returns:
            bool: False (does not suppress exceptions).
        """
        ...
    def close(self) -> None:
        """Close the iterator and release all resources.

        Closes all open directory file descriptors and frees allocated memory.
        After calling close(), the iterator cannot be used anymore.
        This method can be called multiple times safely (idempotent).

        The iterator is automatically closed when garbage collected or when
        used as a context manager, but calling close() explicitly allows for
        deterministic cleanup.

        Raises:
            No exceptions are raised. Multiple calls are safe.
        """
        ...
    def get_stats(self) -> FilesystemIterState:
        """Return current iteration statistics.

        Returns a FilesystemIterState object with current count, bytes, and configuration.

        Raises:
            ValueError: If the iterator has been closed.
        """
        ...
    def skip(self) -> None:
        """Skip recursion into the currently yielded directory.

        Must be called immediately after the iterator yields a directory,
        before calling next() again. Prevents the iterator from recursing
        into the directory that was just yielded.

        Raises:
            ValueError: If the last yielded item was not a directory, or if
                        the iterator has been closed.
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

        Raises:
            ValueError: If the iterator has been closed.
        """
        ...

class IteratorRestoreError(Exception):
    """Exception raised when iterator cannot be restored to previous state.

    Attributes
    ----------
    depth : int
        The directory stack depth (0-indexed) at which restoration failed
    path : str
        The directory path where the expected subdirectory was not found
    """
    depth: int
    path: str

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
    dir_stack: tuple[tuple[str, int], ...] | None = None,
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
        dir_stack: Optional directory stack from previous iteration to restore position.
            Should be obtained from FilesystemIterator.dir_stack(). If provided, iterator
            will navigate to that position and resume iteration from there.

    Returns:
        FilesystemIterator that yields IterInstance objects

    Raises:
        OSError: Filesystem errors (open, statx, statmount failures)
        RuntimeError: Mount validation failures
        NotADirectoryError: Path is not a directory
        TypeError: reporting_callback is not callable
        IteratorRestoreError: Cannot restore to the saved dir_stack position (directory
            not found or inode mismatch). Exception includes depth and path attributes.

    Notes:
        - Uses openat2 with RESOLVE_NO_XDEV | RESOLVE_NO_SYMLINKS
        - Uses statx for extended attributes including birth time
        - GIL is released during I/O operations
        - File descriptors in IterInstance must be closed by caller
        - Reporting callback is invoked with GIL held after every Nth item
        - If callback raises an exception, iteration stops
        - When restoring from dir_stack, directories are not re-yielded, but files
          within the restored directory may be re-yielded (DIR* streams cannot seek)
    """
    ...

# ── NFS4 enums ────────────────────────────────────────────────────────────────

class NFS4AceType(IntEnum):
    ALLOW: int
    DENY: int
    AUDIT: int
    ALARM: int

class NFS4Who(IntEnum):
    """Maps to the (iflag, who) pair in the XDR encoding."""
    NAMED: int     # iflag=0, who=uid/gid
    OWNER: int     # iflag=1, ACE4_SPECIAL_OWNER
    GROUP: int     # iflag=1, ACE4_SPECIAL_GROUP
    EVERYONE: int  # iflag=1, ACE4_SPECIAL_EVERYONE

class NFS4Perm(IntFlag):
    READ_DATA: int
    WRITE_DATA: int
    APPEND_DATA: int
    READ_NAMED_ATTRS: int
    WRITE_NAMED_ATTRS: int
    EXECUTE: int
    DELETE_CHILD: int
    READ_ATTRIBUTES: int
    WRITE_ATTRIBUTES: int
    DELETE: int
    READ_ACL: int
    WRITE_ACL: int
    WRITE_OWNER: int
    SYNCHRONIZE: int

class NFS4Flag(IntFlag):
    FILE_INHERIT: int
    DIRECTORY_INHERIT: int
    NO_PROPAGATE_INHERIT: int
    INHERIT_ONLY: int
    SUCCESSFUL_ACCESS: int
    FAILED_ACCESS: int
    IDENTIFIER_GROUP: int
    INHERITED: int

class NFS4ACLFlag(IntFlag):
    AUTO_INHERIT: int
    PROTECTED: int
    DEFAULTED: int

# ── POSIX enums ───────────────────────────────────────────────────────────────

class POSIXTag(IntEnum):
    USER_OBJ: int
    USER: int
    GROUP_OBJ: int
    GROUP: int
    MASK: int
    OTHER: int

class POSIXPerm(IntFlag):
    EXECUTE: int
    WRITE: int
    READ: int

# ── ACE types ─────────────────────────────────────────────────────────────────

class NFS4Ace:
    """NFS4 Access Control Entry.

    Fields: ace_type (NFS4AceType), ace_flags (NFS4Flag),
    access_mask (NFS4Perm), who_type (NFS4Who), who_id (int).
    who_id is the uid/gid for NAMED entries; -1 for special.
    """
    def __init__(
        self,
        ace_type: NFS4AceType,
        ace_flags: NFS4Flag,
        access_mask: NFS4Perm,
        who_type: NFS4Who,
        who_id: int = -1,
    ) -> None: ...
    @property
    def ace_type(self) -> NFS4AceType: ...
    @property
    def ace_flags(self) -> NFS4Flag: ...
    @property
    def access_mask(self) -> NFS4Perm: ...
    @property
    def who_type(self) -> NFS4Who: ...
    @property
    def who_id(self) -> int: ...
    def __repr__(self) -> str: ...

class POSIXAce:
    """POSIX ACL entry.

    Fields: tag (POSIXTag), perms (POSIXPerm), id (int), default (bool).
    id is the uid/gid for USER/GROUP; -1 for special entries.
    default=True marks entries in the default ACL.
    """
    def __init__(
        self,
        tag: POSIXTag,
        perms: POSIXPerm,
        id: int = -1,
        default: bool = False,
    ) -> None: ...
    @property
    def tag(self) -> POSIXTag: ...
    @property
    def perms(self) -> POSIXPerm: ...
    @property
    def id(self) -> int: ...
    @property
    def default(self) -> bool: ...
    def __repr__(self) -> str: ...

# ── ACL types ─────────────────────────────────────────────────────────────────

class NFS4ACL:
    """NFS4 ACL wrapper (system.nfs4_acl_xdr).

    Constructed from raw big-endian XDR bytes or via from_aces().
    """
    def __init__(self, data: bytes) -> None: ...
    @classmethod
    def from_aces(
        cls,
        aces: Iterable[NFS4Ace],
        acl_flags: NFS4ACLFlag = ...,
    ) -> NFS4ACL: ...
    @property
    def acl_flags(self) -> NFS4ACLFlag: ...
    @property
    def aces(self) -> list[NFS4Ace]: ...
    def __bytes__(self) -> bytes: ...
    def __len__(self) -> int: ...
    def __repr__(self) -> str: ...

class POSIXACL:
    """POSIX1E ACL wrapper.

    Constructed from raw little-endian xattr bytes or via from_aces().
    """
    def __init__(
        self,
        access_data: bytes,
        default_data: bytes | None = None,
    ) -> None: ...
    @classmethod
    def from_aces(cls, aces: Iterable[POSIXAce]) -> POSIXACL: ...
    @property
    def aces(self) -> list[POSIXAce]: ...
    @property
    def default_aces(self) -> list[POSIXAce]: ...
    def access_bytes(self) -> bytes: ...
    def default_bytes(self) -> bytes | None: ...
    def __repr__(self) -> str: ...

# ── ACL functions ─────────────────────────────────────────────────────────────

def fgetacl(fd: int) -> NFS4ACL | POSIXACL:
    """Get the ACL on an open file descriptor.

    Returns NFS4ACL for NFS4/ZFS filesystems, POSIXACL for POSIX1E.
    Raises OSError(EOPNOTSUPP) if ACLs are disabled on the filesystem.
    """
    ...

def fsetacl(fd: int, acl: NFS4ACL | POSIXACL) -> None:
    """Set the ACL on an open file descriptor.

    acl must match the ACL type supported by the filesystem.
    Raises OSError on failure, TypeError if acl is not NFS4ACL or POSIXACL.
    """
    ...

def fsetacl_nfs4(fd: int, data: bytes) -> None:
    """Set system.nfs4_acl_xdr from raw XDR bytes.  Low-level interface."""
    ...

def fsetacl_posix(
    fd: int,
    access_bytes: bytes,
    default_bytes: bytes | None,
) -> None:
    """Set POSIX ACL xattrs from raw bytes.  Low-level interface."""
    ...
