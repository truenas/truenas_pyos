"""Type stubs for truenas_os module.

This module provides Python bindings to Linux kernel system calls for
advanced filesystem and mount operations, plus ACL support.
"""

from typing import Any, Callable, ClassVar, Iterable, Iterator, Literal, NamedTuple, final, type_check_only
from enum import IntEnum, IntFlag

# StatxResult type - PyStructSequence from statx(2)
@final
class StatxResult(tuple[Any, ...]):  # PyStructSequence, not a true NamedTuple
    """Extended file attributes from statx(2) system call.

    A struct-sequence containing file metadata.
    Fields not requested or unavailable may be 0 or unset.
    """
    n_fields: ClassVar[int]
    n_sequence_fields: ClassVar[int]
    n_unnamed_fields: ClassVar[int]
    __match_args__: ClassVar[tuple[str, ...]]
    def __replace__(self, /, **changes: Any) -> StatxResult: ...
    @property
    def stx_mask(self) -> int: ...
    @property
    def stx_blksize(self) -> int: ...
    @property
    def stx_attributes(self) -> int: ...
    @property
    def stx_nlink(self) -> int: ...
    @property
    def stx_uid(self) -> int: ...
    @property
    def stx_gid(self) -> int: ...
    @property
    def stx_mode(self) -> int: ...
    @property
    def stx_ino(self) -> int: ...
    @property
    def stx_size(self) -> int: ...
    @property
    def stx_blocks(self) -> int: ...
    @property
    def stx_attributes_mask(self) -> int: ...
    @property
    def stx_atime(self) -> float: ...  # Seconds since epoch with fractional part
    @property
    def stx_atime_ns(self) -> int: ...  # Nanoseconds since epoch
    @property
    def stx_btime(self) -> float: ...  # Birth/creation time
    @property
    def stx_btime_ns(self) -> int: ...
    @property
    def stx_ctime(self) -> float: ...  # Status change time
    @property
    def stx_ctime_ns(self) -> int: ...
    @property
    def stx_mtime(self) -> float: ...  # Modification time
    @property
    def stx_mtime_ns(self) -> int: ...
    @property
    def stx_rdev_major(self) -> int: ...
    @property
    def stx_rdev_minor(self) -> int: ...
    @property
    def stx_rdev(self) -> int: ...
    @property
    def stx_dev_major(self) -> int: ...
    @property
    def stx_dev_minor(self) -> int: ...
    @property
    def stx_dev(self) -> int: ...
    @property
    def stx_mnt_id(self) -> int: ...
    @property
    def stx_dio_mem_align(self) -> int: ...
    @property
    def stx_dio_offset_align(self) -> int: ...
    @property
    def stx_subvol(self) -> int: ...
    @property
    def stx_atomic_write_unit_min(self) -> int: ...
    @property
    def stx_atomic_write_unit_max(self) -> int: ...
    @property
    def stx_atomic_write_segments_max(self) -> int: ...

# StatmountResult type - PyStructSequence from statmount(2)
@final
class StatmountResult(tuple[Any, ...]):  # PyStructSequence, not a true NamedTuple
    """Mount point information from statmount(2) system call.

    A struct-sequence containing mount metadata.
    Fields not requested will be None.

    Note: optional fields (fs_subtype, sb_source, opt_array, opt_sec_array,
    supported_mask, mnt_uidmap, mnt_gidmap) are only present when the kernel
    supports the corresponding STATMOUNT_* constant.  They are absent from
    this build.
    """
    n_fields: ClassVar[int]
    n_sequence_fields: ClassVar[int]
    n_unnamed_fields: ClassVar[int]
    __match_args__: ClassVar[tuple[str, ...]]
    def __replace__(self, /, **changes: Any) -> StatmountResult: ...
    @property
    def mnt_id(self) -> int | None: ...
    @property
    def mnt_parent_id(self) -> int | None: ...
    @property
    def mnt_id_old(self) -> int | None: ...
    @property
    def mnt_parent_id_old(self) -> int | None: ...
    @property
    def mnt_root(self) -> str | None: ...
    @property
    def mnt_point(self) -> str | None: ...
    @property
    def mnt_attr(self) -> int | None: ...
    @property
    def mnt_propagation(self) -> int | None: ...
    @property
    def mnt_peer_group(self) -> int | None: ...
    @property
    def mnt_master(self) -> int | None: ...
    @property
    def propagate_from(self) -> int | None: ...
    @property
    def fs_type(self) -> str | None: ...
    @property
    def mnt_ns_id(self) -> int | None: ...
    @property
    def mnt_opts(self) -> str | None: ...
    @property
    def sb_dev_major(self) -> int | None: ...
    @property
    def sb_dev_minor(self) -> int | None: ...
    @property
    def sb_magic(self) -> int | None: ...
    @property
    def sb_flags(self) -> int | None: ...
    @property
    def fs_subtype(self) -> str | None: ...  # Present on kernels with STATMOUNT_FS_SUBTYPE
    @property
    def sb_source(self) -> str | None: ...   # Present on kernels with STATMOUNT_SB_SOURCE
    @property
    def opt_array(self) -> list[str] | None: ...     # Present on kernels with STATMOUNT_OPT_ARRAY
    @property
    def opt_sec_array(self) -> list[str] | None: ... # Present on kernels with STATMOUNT_OPT_SEC_ARRAY
    @property
    def supported_mask(self) -> int | None: ...  # Present on kernels with STATMOUNT_SUPPORTED_MASK
    @property
    def mnt_uidmap(self) -> str | None: ...  # Present on kernels with STATMOUNT_MNT_UIDMAP
    @property
    def mnt_gidmap(self) -> str | None: ...  # Present on kernels with STATMOUNT_MNT_GIDMAP
    @property
    def mask(self) -> int: ...

# statx function
def statx(
    path: str | bytes,
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
    flags: int,
    dir_fd: int = ...,  # Default: AT_FDCWD
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
    mount_id: int,
    flags: int = ...,  # Default: O_DIRECTORY
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

# open_tree function
def open_tree(
    *,
    path: str,
    dir_fd: int = ...,  # Default: AT_FDCWD
    flags: int = 0,
) -> int:
    """Open a mount or directory tree.

    Parameters
    ----------
    path : str
        Path to the mount or directory (can be relative to dir_fd)
    dir_fd : int, optional
        Directory file descriptor (default: AT_FDCWD)
    flags : int, optional
        Flags (OPEN_TREE_* and AT_* constants)

    Returns
    -------
    int
        File descriptor representing the mount tree
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
    flags: int,
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
STATX_DIO_READ_ALIGN: int  # Kernel 6.x+
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

# SB flags
SB_RDONLY: int

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
FH_AT_HANDLE_MNT_ID_UNIQUE: int

# OPEN_TREE constants (for open_tree)
OPEN_TREE_CLONE: int    # 0x1 — Create a detached clone of the mount tree
OPEN_TREE_CLOEXEC: int  # 0x80000 — Set close-on-exec on the returned fd

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

# MS_* mount(2) flags
MS_RDONLY: int       # 0x00000001 — Mount read-only
MS_NOSUID: int       # 0x00000002 — Ignore suid and sgid bits
MS_NODEV: int        # 0x00000004 — Disallow access to device special files
MS_NOEXEC: int       # 0x00000008 — Disallow program execution
MS_SYNCHRONOUS: int  # 0x00000010 — Writes are synced at once
MS_REMOUNT: int      # 0x00000020 — Alter flags of a mounted filesystem
MS_DIRSYNC: int      # 0x00000080 — Directory modifications are synchronous
MS_NOSYMFOLLOW: int  # 0x00000100 — Do not follow symlinks
MS_NOATIME: int      # 0x00000400 — Do not update access times
MS_NODIRATIME: int   # 0x00000800 — Do not update directory access times
MS_BIND: int         # 0x00001000 — Bind mount
MS_MOVE: int         # 0x00002000 — Move a subtree
MS_REC: int          # 0x00004000 — Recursive bind mount
MS_PRIVATE: int      # 0x00040000 — Change to private
MS_SLAVE: int        # 0x00080000 — Change to slave
MS_SHARED: int       # 0x00100000 — Change to shared
MS_RELATIME: int     # 0x00200000 — Update atime relative to mtime/ctime
MS_STRICTATIME: int  # 0x01000000 — Always perform atime updates
MS_LAZYTIME: int     # 0x02000000 — Update times lazily on disk
MS_UNBINDABLE: int   # 0x00020000 — Change to unbindable

# fhandle type
class fhandle:
    """File handle object for name_to_handle_at and open_by_handle_at operations."""
    def __init__(
        self,
        /,
        handle_bytes: bytes | None = None,
        mount_id: int | None = None,
        unique_mount_id: bool = False,
    ) -> None: ...
    @property
    def mount_id(self) -> int | None: ...
    def open(self, mount_fd: int, flags: int = 0, /) -> int: ...
    def __bytes__(self) -> bytes: ...
    def __repr__(self) -> str: ...

# Filesystem iterator types
@final
class IterInstance(tuple[Any, ...]):  # PyStructSequence, not a true NamedTuple
    """Instance returned by filesystem iterator.

    Represents a file or directory encountered during iteration.

    The file descriptor must NOT be closed by the caller.

    The iterator manages the file descriptor lifecycle — it is closed automatically
    at the start of the next iteration or when the iterator's context manager exits.
    """
    n_fields: ClassVar[int]
    n_sequence_fields: ClassVar[int]
    n_unnamed_fields: ClassVar[int]
    __match_args__: ClassVar[tuple[
        Literal['parent'], Literal['name'], Literal['fd'],
        Literal['statxinfo'], Literal['isdir'], Literal['islnk'], Literal['isreg'],
    ]]
    def __replace__(self, /, **changes: Any) -> IterInstance: ...
    @property
    def parent(self) -> str: ...  # Parent directory path
    @property
    def name(self) -> str: ...  # Entry name
    @property
    def fd(self) -> int: ...  # Open file descriptor
    @property
    def statxinfo(self) -> StatxResult: ...  # Extended file attributes
    @property
    def isdir(self) -> bool: ...  # True if directory, False otherwise
    @property
    def islnk(self) -> bool: ...  # True if symlink, False otherwise
    @property
    def isreg(self) -> bool: ...  # True if regular file, False otherwise

@final
class FilesystemIterState(tuple[Any, ...]):  # PyStructSequence, not a true NamedTuple
    """State for filesystem iteration.

    Tracks iteration progress and configuration.
    """
    n_fields: ClassVar[int]
    n_sequence_fields: ClassVar[int]
    n_unnamed_fields: ClassVar[int]
    __match_args__: ClassVar[tuple[
        Literal['cnt'], Literal['cnt_bytes'], Literal['current_directory'],
    ]]
    def __replace__(self, /, **changes: Any) -> FilesystemIterState: ...
    @property
    def cnt(self) -> int: ...  # Count of items yielded
    @property
    def cnt_bytes(self) -> int: ...  # Total bytes of files yielded
    @property
    def current_directory(self) -> str: ...  # Current directory path

@type_check_only
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
    relative_path: str | None = None,
    /,
    btime_cutoff: int = 0,
    cnt: int = 0,
    cnt_bytes: int = 0,
    file_open_flags: int = ...,
    reporting_increment: int = 1000,
    reporting_callback: Callable[[tuple[tuple[str, int], ...], FilesystemIterState, Any], Any] | None = None,
    reporting_private_data: Any = None,
    dir_stack: tuple[tuple[str, int], ...] | None = None,
    include_symlinks: bool = False,
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
        include_symlinks: When True, symlink entries are yielded with
            ``IterInstance.islnk == True``.  Their fd is an O_PATH | O_NOFOLLOW
            descriptor — usable with ``statx(fd, "", AT_EMPTY_PATH)`` and
            with ``os.readlink("", dir_fd=fd)`` to read the target, but not
            with read/write.  When False (default), symlinks are silently
            skipped.  Symlinks are never traversed.

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
    ALLOW = 0x0
    DENY = 0x1
    AUDIT = 0x2
    ALARM = 0x3

class NFS4Who(IntEnum):
    """Maps to the (iflag, who) pair in the XDR encoding."""
    NAMED = 0     # iflag=0, who=uid/gid
    OWNER = 1     # iflag=1, ACE4_SPECIAL_OWNER
    GROUP = 2     # iflag=1, ACE4_SPECIAL_GROUP
    EVERYONE = 3  # iflag=1, ACE4_SPECIAL_EVERYONE

class NFS4Perm(IntFlag):
    READ_DATA = 0x000001
    WRITE_DATA = 0x000002
    APPEND_DATA = 0x000004
    READ_NAMED_ATTRS = 0x000008
    WRITE_NAMED_ATTRS = 0x000010
    EXECUTE = 0x000020
    DELETE_CHILD = 0x000040
    READ_ATTRIBUTES = 0x000080
    WRITE_ATTRIBUTES = 0x000100
    DELETE = 0x010000
    READ_ACL = 0x020000
    WRITE_ACL = 0x040000
    WRITE_OWNER = 0x080000
    SYNCHRONIZE = 0x100000

class NFS4Flag(IntFlag):
    FILE_INHERIT = 0x01
    DIRECTORY_INHERIT = 0x02
    NO_PROPAGATE_INHERIT = 0x04
    INHERIT_ONLY = 0x08
    SUCCESSFUL_ACCESS = 0x10
    FAILED_ACCESS = 0x20
    IDENTIFIER_GROUP = 0x40
    INHERITED = 0x80

class NFS4ACLFlag(IntFlag):
    AUTO_INHERIT = 0x000001
    PROTECTED = 0x000002
    DEFAULTED = 0x000004
    ACL_IS_TRIVIAL = 0x010000
    ACL_IS_DIR = 0x020000

# ── POSIX enums ───────────────────────────────────────────────────────────────

class POSIXTag(IntEnum):
    USER_OBJ = 0x01
    USER = 0x02
    GROUP_OBJ = 0x04
    GROUP = 0x08
    MASK = 0x10
    OTHER = 0x20

class POSIXPerm(IntFlag):
    EXECUTE = 0x1
    WRITE = 0x2
    READ = 0x4

# ── ACE types ─────────────────────────────────────────────────────────────────

@final
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

@final
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

@final
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
    @property
    def trivial(self) -> bool: ...
    def generate_inherited_acl(self, is_dir: bool = False) -> NFS4ACL: ...
    def __bytes__(self) -> bytes: ...
    def __len__(self) -> int: ...
    def __repr__(self) -> str: ...

@final
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
    @property
    def trivial(self) -> bool: ...
    def generate_inherited_acl(self, is_dir: bool = True) -> POSIXACL: ...
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

def validate_acl(fd: int, acl: NFS4ACL | POSIXACL) -> None:
    """Validate an ACL against an open file descriptor without setting it.

    Runs the same checks as fsetacl() but does not write the ACL.
    Pass fd=-1 to skip all filesystem operations and validate as if the
    target is a directory (inherit flags and default ACLs are permitted).
    Raises ValueError if the ACL is invalid, OSError if fstat(fd) fails,
    TypeError if acl is not NFS4ACL or POSIXACL.
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

# ── Generic xattr functions ──────────────────────────────────────────────────

def fgetxattr(fd: int, name: str) -> bytes:
    """Read the extended attribute `name` from an open file descriptor.

    Raises ``OSError(ENODATA)`` if the attribute is absent and
    ``OSError(E2BIG)`` if the value exceeds ``XATTR_SIZE_MAX``.
    """
    ...

def fsetxattr(
    fd: int,
    name: str,
    value: bytes,
    *,
    flags: int = 0,
) -> None:
    """Set the extended attribute `name` to `value` on an open fd.

    `flags` must be 0, ``XATTR_CREATE``, or ``XATTR_REPLACE``; any
    other value raises ``ValueError``.  Raises ``OSError(E2BIG)`` if
    ``len(value)`` exceeds ``XATTR_SIZE_MAX``.
    """
    ...

def flistxattr(fd: int) -> list[str]:
    """Return the list of extended attribute names on an open fd.

    Raises ``OSError(E2BIG)`` if the cumulative name list exceeds
    ``XATTR_SIZE_MAX``.
    """
    ...

XATTR_CREATE: int    # 1 — fail if attribute exists
XATTR_REPLACE: int   # 2 — fail if attribute does not exist
XATTR_SIZE_MAX: int  # 2 * 1024 * 1024 — TrueNAS xattr value cap
