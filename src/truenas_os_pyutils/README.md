# truenas_os_pyutils

Pure-Python utilities that build on the `truenas_os` C extension. Requires
Linux kernel 6.8 or later for `statmount(2)`/`listmount(2)`/`openat2(2)`
support; kernel 6.9 or later for `PIDFD_GET_USER_NAMESPACE` (used by
`namespace.py`); kernel 6.18 or later for `STATMOUNT_SB_SOURCE`
(`mount_source` field and ZFS snapshot detection in `mount.py`).

---

## `io.py`

Symlink-safe file I/O using `openat2(RESOLVE_NO_SYMLINKS)`.

| Name | Type | Description |
|---|---|---|
| `SymlinkInPathError` | exception | Raised when a symlink is detected in a path. Subclass of `OSError` with `errno=ELOOP`. |
| `safe_open(path, mode, ..., dir_fd)` | context manager | Drop-in for `open()` that rejects symlinks in any path component. |
| `atomic_write(target, mode, *, tmppath, uid, gid, perms, noclobber)` | context manager | Yields a file object; atomically replaces `target` on clean exit using `renameat2(AT_RENAME_EXCHANGE)`. With `noclobber=True`, fails (`FileExistsError`) if `target` already exists and uses `AT_RENAME_NOREPLACE` to also fail on rename-time races. |
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

## `namespace.py`

User-namespace helpers for idmapped mounts. Thin context-manager wrapper
around `truenas_os.create_idmap_userns`; the C-level dance (clone3 +
`/proc/<pid>/{setgroups,uid_map,gid_map}` writes + `PIDFD_GET_USER_NAMESPACE`
ioctl + SIGKILL + waitpid) is GIL-free.

| Name | Type | Description |
|---|---|---|
| `idmap_userns(uid_map, gid_map)` | context manager | Creates a userns with the given maps; yields a fd that pins it; closes the fd on exit. Both arguments are `Iterable[truenas_os.IdmapMappingEntry]` — build entries with `truenas_os.create_idmap_mapping(inside, outside, length)` for validated construction (range and overflow checks). Raises `OSError` on kernel-level failure, `TypeError` on raw-tuple input, `ValueError` on empty input. |

Privileged (non-identity) maps require `CAP_SETUID` and `CAP_SETGID` in the
parent user namespace. Root in `init_user_ns` trivially satisfies that.
Without those caps the kernel restricts each map to a single line mapping
the caller's own EUID/EGID 1:1.

Requires Linux ≥ 6.9 for `PIDFD_GET_USER_NAMESPACE`.

### Example: idmapped bind-mount for a container rootfs

Set up an unprivileged-LXC rootfs with container UIDs `[0, 65536)` mapped
to host UIDs `[100000, 165536)`:

```python
import os
import truenas_os
from truenas_os_pyutils.namespace import idmap_userns

uid_entries = [truenas_os.create_idmap_mapping(0, 100000, 65536)]
gid_entries = [truenas_os.create_idmap_mapping(0, 100000, 65536)]

source = "/mnt/tank/container"
target = "/run/containers/root/<uuid>"
os.makedirs(target, exist_ok=True)

with idmap_userns(uid_entries, gid_entries) as userns_fd:
    tree_fd = truenas_os.open_tree(
        path=source,
        flags=truenas_os.OPEN_TREE_CLONE | truenas_os.OPEN_TREE_CLOEXEC,
    )
    try:
        # Bind the idmap to the detached clone.
        truenas_os.mount_setattr(
            path="", dirfd=tree_fd,
            attr_set=truenas_os.MOUNT_ATTR_IDMAP,
            userns_fd=userns_fd,
            flags=truenas_os.AT_EMPTY_PATH,
        )
        # Defensive slave propagation so a container-side umount of a
        # sub-mount can't propagate back to the host source.
        truenas_os.mount_setattr(
            path="", dirfd=tree_fd,
            propagation=truenas_os.MS_SLAVE,
            flags=truenas_os.AT_EMPTY_PATH,
        )
        # Attach the now-idmapped clone at the container's rootfs target.
        truenas_os.move_mount(
            from_path="", from_dirfd=tree_fd,
            to_path=target,
            flags=truenas_os.MOVE_MOUNT_F_EMPTY_PATH,
        )
    finally:
        os.close(tree_fd)

# A file owned by UID 0 inside `source` now appears as UID 100000 when
# read through `target`. The idmap survives the `with` block — closing
# the userns fd doesn't unbind the attached mount.
```

The `idmap_userns` block can be short-lived: the userns fd just needs to
be live during `mount_setattr(MOUNT_ATTR_IDMAP)`; the kernel takes a
reference to the userns that survives until the mount is gone.

---

## `truenas_shutil/`

Recursive file-tree copy plus the file-level copy/clone primitives.
Provides `copytree`, `CopyTreeConfig`, and the standalone helpers
(`copyfile`, `clonefile`, `copysendfile`, …) — see
[`truenas_shutil/README.md`](truenas_shutil/README.md) for the full API.
