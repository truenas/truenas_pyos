from _typeshed import Incomplete
from typing import Any, ClassVar, overload

AT_EMPTY_PATH: int
AT_FDCWD: int
AT_NO_AUTOMOUNT: int
AT_REMOVEDIR: int
AT_STATX_DONT_SYNC: int
AT_STATX_FORCE_SYNC: int
AT_STATX_SYNC_AS_STAT: int
AT_SYMLINK_FOLLOW: int
AT_SYMLINK_NOFOLLOW: int
FH_AT_EMPTY_PATH: int
FH_AT_HANDLE_CONNECTABLE: int
FH_AT_HANDLE_FID: int
FH_AT_SYMLINK_FOLLOW: int
MOUNT_ATTR_IDMAP: int
MOUNT_ATTR_NOATIME: int
MOUNT_ATTR_NODEV: int
MOUNT_ATTR_NODIRATIME: int
MOUNT_ATTR_NOEXEC: int
MOUNT_ATTR_NOSUID: int
MOUNT_ATTR_NOSYMFOLLOW: int
MOUNT_ATTR_RDONLY: int
MOUNT_ATTR_RELATIME: int
MOUNT_ATTR_STRICTATIME: int
MOUNT_ATTR__ATIME: int
MOVE_MOUNT_BENEATH: int
MOVE_MOUNT_F_AUTOMOUNTS: int
MOVE_MOUNT_F_EMPTY_PATH: int
MOVE_MOUNT_F_SYMLINKS: int
MOVE_MOUNT_SET_GROUP: int
MOVE_MOUNT_T_AUTOMOUNTS: int
MOVE_MOUNT_T_EMPTY_PATH: int
MOVE_MOUNT_T_SYMLINKS: int
RESOLVE_BENEATH: int
RESOLVE_CACHED: int
RESOLVE_IN_ROOT: int
RESOLVE_NO_MAGICLINKS: int
RESOLVE_NO_SYMLINKS: int
RESOLVE_NO_XDEV: int
STATMOUNT_ALL: int
STATMOUNT_FS_SUBTYPE: int
STATMOUNT_FS_TYPE: int
STATMOUNT_MNT_BASIC: int
STATMOUNT_MNT_GIDMAP: int
STATMOUNT_MNT_NS_ID: int
STATMOUNT_MNT_OPTS: int
STATMOUNT_MNT_POINT: int
STATMOUNT_MNT_ROOT: int
STATMOUNT_MNT_UIDMAP: int
STATMOUNT_OPT_ARRAY: int
STATMOUNT_OPT_SEC_ARRAY: int
STATMOUNT_PROPAGATE_FROM: int
STATMOUNT_SB_BASIC: int
STATMOUNT_SB_SOURCE: int
STATMOUNT_SUPPORTED_MASK: int
STATX_ALL: int
STATX_ATIME: int
STATX_ATTR_APPEND: int
STATX_ATTR_AUTOMOUNT: int
STATX_ATTR_COMPRESSED: int
STATX_ATTR_DAX: int
STATX_ATTR_ENCRYPTED: int
STATX_ATTR_IMMUTABLE: int
STATX_ATTR_MOUNT_ROOT: int
STATX_ATTR_NODUMP: int
STATX_ATTR_VERITY: int
STATX_ATTR_WRITE_ATOMIC: int
STATX_BASIC_STATS: int
STATX_BLOCKS: int
STATX_BTIME: int
STATX_CTIME: int
STATX_DIOALIGN: int
STATX_DIO_READ_ALIGN: int
STATX_GID: int
STATX_INO: int
STATX_MNT_ID: int
STATX_MNT_ID_UNIQUE: int
STATX_MODE: int
STATX_MTIME: int
STATX_NLINK: int
STATX_SIZE: int
STATX_SUBVOL: int
STATX_TYPE: int
STATX_UID: int
STATX_WRITE_ATOMIC: int
STATX__RESERVED: int

class StatmountResult(tuple):
    n_fields: ClassVar[int] = ...
    n_sequence_fields: ClassVar[int] = ...
    n_unnamed_fields: ClassVar[int] = ...
    __match_args__: ClassVar[tuple] = ...
    fs_subtype: Incomplete
    fs_type: Incomplete
    mask: Incomplete
    mnt_attr: Incomplete
    mnt_gidmap: Incomplete
    mnt_id: Incomplete
    mnt_id_old: Incomplete
    mnt_master: Incomplete
    mnt_ns_id: Incomplete
    mnt_opts: Incomplete
    mnt_parent_id: Incomplete
    mnt_parent_id_old: Incomplete
    mnt_peer_group: Incomplete
    mnt_point: Incomplete
    mnt_propagation: Incomplete
    mnt_root: Incomplete
    mnt_uidmap: Incomplete
    opt_array: Incomplete
    opt_sec_array: Incomplete
    propagate_from: Incomplete
    sb_dev_major: Incomplete
    sb_dev_minor: Incomplete
    sb_flags: Incomplete
    sb_magic: Incomplete
    sb_source: Incomplete
    supported_mask: Incomplete
    @classmethod
    def __init__(cls, *args, **kwargs) -> None: ...
    def __reduce__(self): ...
    def __replace__(self, *args, **kwargs): ...

class StatxResult(tuple):
    n_fields: ClassVar[int] = ...
    n_sequence_fields: ClassVar[int] = ...
    n_unnamed_fields: ClassVar[int] = ...
    __match_args__: ClassVar[tuple] = ...
    stx_atime: Incomplete
    stx_atime_ns: Incomplete
    stx_atomic_write_segments_max: Incomplete
    stx_atomic_write_unit_max: Incomplete
    stx_atomic_write_unit_max_opt: Incomplete
    stx_atomic_write_unit_min: Incomplete
    stx_attributes: Incomplete
    stx_attributes_mask: Incomplete
    stx_blksize: Incomplete
    stx_blocks: Incomplete
    stx_btime: Incomplete
    stx_btime_ns: Incomplete
    stx_ctime: Incomplete
    stx_ctime_ns: Incomplete
    stx_dev: Incomplete
    stx_dev_major: Incomplete
    stx_dev_minor: Incomplete
    stx_dio_mem_align: Incomplete
    stx_dio_offset_align: Incomplete
    stx_dio_read_offset_align: Incomplete
    stx_gid: Incomplete
    stx_ino: Incomplete
    stx_mask: Incomplete
    stx_mnt_id: Incomplete
    stx_mode: Incomplete
    stx_mtime: Incomplete
    stx_mtime_ns: Incomplete
    stx_nlink: Incomplete
    stx_rdev: Incomplete
    stx_rdev_major: Incomplete
    stx_rdev_minor: Incomplete
    stx_size: Incomplete
    stx_subvol: Incomplete
    stx_uid: Incomplete
    @classmethod
    def __init__(cls, *args, **kwargs) -> None: ...
    def __reduce__(self): ...
    def __replace__(self, *args, **kwargs): ...

class fhandle:
    mount_id: Incomplete
    def __init__(self, *args, **kwargs) -> None: ...
    def open(self, *args, **kwargs): ...
    def __bytes__(self) -> bytes: ...

@overload
def iter_mount() -> Any: ...
@overload
def iter_mount(statmount_flags=...) -> Any: ...
def listmount() -> Any: ...
def move_mount() -> Any: ...
def open_mount_by_id(*args, **kwargs): ...
def openat2() -> Any: ...
def statmount() -> Any: ...
def statx() -> Any: ...
