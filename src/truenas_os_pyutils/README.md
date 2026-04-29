# truenas_os_pyutils

Pure-Python utilities that build on the `truenas_os` C extension. Requires
Linux kernel 6.8 or later for `statmount(2)`/`listmount(2)`/`openat2(2)`
support; kernel 6.18 or later for `STATMOUNT_SB_SOURCE` (`mount_source` field
and ZFS snapshot detection in `mount.py`).

---

## `io.py`

Symlink-safe file I/O using `openat2(RESOLVE_NO_SYMLINKS)`.

| Name | Type | Description |
|---|---|---|
| `SymlinkInPathError` | exception | Raised when a symlink is detected in a path. Subclass of `OSError` with `errno=ELOOP`. |
| `safe_open(path, mode, ..., dir_fd)` | context manager | Drop-in for `open()` that rejects symlinks in any path component. |
| `atomic_write(target, mode, *, tmppath, uid, gid, perms)` | context manager | Yields a file object; atomically replaces `target` on clean exit using `renameat2(AT_RENAME_EXCHANGE)`. |
| `atomic_replace(*, temp_path, target_file, data, uid, gid, perms)` | function | Writes `data` to `target_file` atomically. Thin wrapper around `atomic_write`. |

All write operations use `RESOLVE_NO_SYMLINKS` for TOCTOU protection. Pass
`uid=-1` or `gid=-1` to preserve the existing file's ownership.

---

## `mount.py`

Mount point enumeration and unmounting via `statmount(2)`.

| Name | Type | Description |
|---|---|---|
| `StatmountResultDict` | TypedDict | Dict representation of a mount point. Keys: `mount_id`, `parent_id`, `device_id`, `root`, `mountpoint`, `mount_opts`, `fs_type`, `mount_source`, `super_opts`. |
| `statmount(*, path, fd, as_dict)` | function | Returns mount information for the filesystem containing `path` or open `fd`. |
| `iter_mountinfo(*, target_mnt_id, path, fd, reverse, as_dict, include_snapshot_mounts)` | generator | Iterates all mounts, optionally restricted to children of a given mount. |
| `umount(path, *, force, detach, expire, follow_symlinks, recursive)` | function | Unmounts the filesystem at `path`, optionally recursing into child mounts first. |

`statmount` and `iter_mountinfo` both accept `path` or `fd` to scope results.
Symlinks in `path` raise `SymlinkInPathError`. ZFS snapshot mounts are excluded
from `iter_mountinfo` by default; pass `include_snapshot_mounts=True` to
include them.

A `RuntimeWarning` is emitted at import time if the package was built without
`STATMOUNT_SB_SOURCE` support (kernel < 6.18); `mount_source` will be `None`
and ZFS snapshot detection will be disabled in that case.

---

## `truenas_shutil/`

Recursive file-tree copy plus the file-level copy/clone primitives.
Provides `copytree`, `CopyTreeConfig`, and the standalone helpers
(`copyfile`, `clonefile`, `copysendfile`, …) — see
[`truenas_shutil/README.md`](truenas_shutil/README.md) for the full API.
