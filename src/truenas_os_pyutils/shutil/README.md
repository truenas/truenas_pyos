# truenas_os_pyutils.shutil

Recursive file-tree copy plus the file-level copy/clone primitives that
back it.  Public symbols are re-exported from `truenas_os_pyutils.shutil`,
so callers can write `from truenas_os_pyutils.shutil import copytree`
without reaching into the submodules.

Driven by `truenas_os.iter_filesystem_contents` (depth-first, GIL released,
mountpoint-validated).  Mirrors the `AclTool` pattern in
`middleware/plugins/filesystem_/utils.py` — same fsiter + `iter_mount`
mechanism for cross-mount recursion.

---

## `copy.py` — file-level primitives

Standalone primitives that operate on a single source/destination fd pair.
No tree traversal, no mount validation — useful on their own when you
already have open fds.

| Name | Type | Description |
|---|---|---|
| `copy_permissions(src_fd, dst_fd, xattrs, mode)` | function | Replicate POSIX mode or ACL xattrs. |
| `copy_xattrs(src_fd, dst_fd, xattrs)` | function | Copy non-ACL extended attributes. |
| `clone_file(src_fd, dst_fd)` | function | Block-level clone via `copy_file_range(2)` (raises `EXDEV` across filesystems). |
| `clone_or_copy_file(src_fd, dst_fd)` | function | Try `clone_file`; on `EXDEV` fall back to `copy_sendfile`. |
| `copy_sendfile(src_fd, dst_fd)` | function | Zero-copy via `sendfile(2)` with userspace fallback. |
| `copy_file_userspace(src_fd, dst_fd)` | function | Pure userspace copy via `shutil.copyfileobj`. |
| `MAX_RW_SZ` | int | Maximum kernel read/write size (`INT_MAX & ~4096`). |
| `ACL_XATTRS`, `ACCESS_ACL_XATTRS` | frozenset | xattr names that hold ACL data. |

`copy_permissions` skips `fchmod` if the source advertises an access ACL
xattr — copying the xattr controls permissions in that case.  ZFS
`aclmode=restricted` causes `fchmod` to raise `PermissionError` when the
destination already inherited an ACL; callers should be prepared for that.

`copy_xattrs` skips `system.*` xattrs (filesystem-specific handlers that
do not round-trip).

---

## `copytree.py` — recursive copy

Tree-level orchestration on top of `copy.py` and
`truenas_os.iter_filesystem_contents`.

| Name | Type | Description |
|---|---|---|
| `CopyFlags` | `IntFlag` | Bitmask of metadata to preserve: `XATTRS`, `PERMISSIONS`, `TIMESTAMPS`, `OWNER`. |
| `CopyTreeOp` | enum | Per-file copy strategy: `DEFAULT` (clone, falling back to sendfile and userspace), `CLONE`, `SENDFILE`, `USERSPACE`. |
| `ReportingCallback` | type alias | Same shape as fsiter's `reporting_callback`: `Callable[[dir_stack, FilesystemIterState, private_data], Any]`. |
| `CopyTreeConfig` | dataclass | Immutable copy configuration: `reporting_callback`, `reporting_private_data`, `reporting_increment`, `raise_error`, `exist_ok`, `traverse`, `op`, `flags`. |
| `CopyTreeStats` | dataclass | Mutable counters returned from `copytree`: `dirs`, `files`, `symlinks`, `bytes`. |
| `DEF_CP_FLAGS` | `CopyFlags` | Default flag combination — all four metadata bits. |
| `copytree(src, dst, config)` | function | Recursively copy `src` into `dst`. |

### Behavior

`copytree` opens `src` and `dst` with `openat2(RESOLVE_NO_SYMLINKS)` and
resolves the source mountpoint via `statmount` before invoking fsiter.
It returns a `CopyTreeStats`.

The `reporting_callback` / `reporting_private_data` / `reporting_increment`
fields are forwarded to fsiter unchanged; callers wire up whatever
progress/throttling/logging they want in their own callable.

### Cross-mount recursion (`traverse=True`)

After the root pass, child mounts under `src` are enumerated via
`truenas_os.iter_mount` and processed in turn.  Each child mount runs
`_process_mount` against its own mountpoint + source name.  ZFS snapshot
mounts (detected as `fs_type == "zfs"` with `@` in the source name) are
always skipped — they are read-only and transient, so destination writes
would fail with `EROFS` or expire mid-copy.

### `.zfs` ctldir

Detected by inode (`0x0000FFFFFFFFFFFF`) and excluded from the copy.
Skipping happens via `it.skip()` so the iterator does not descend into
the snapshot tree.

### Self-into-self protection

When `src` contains `dst`, the destination directory entry would otherwise
be copied into itself.  We detect this via `dev_t` + inode match against
the destination root and call `it.skip()` on that subtree.

### Symlinks

`iter_filesystem_contents` opens entries with `O_NOFOLLOW`, which fails
`ELOOP` on symlinks; the C extension silently prunes those entries at
`fsiter.c:324`.  `copytree` therefore does a small `os.scandir` pass per
directory it visits, recreating any symlinks in the destination.  This
adds one `scandir` per visited directory; symlink targets are preserved
verbatim (no path translation).

### Directory timestamps on ascent

Directory `atime`/`mtime` are applied when the destination directory is
popped from the frame stack — i.e. *after* its children have been copied.
This prevents children's writes from bumping the parent's timestamps.
The original `copy.py` from middleware does the same in its recursive
form; we replicate it via the frame stack here.

### Frame stack invariant

For each level of source-side directory the iterator descends into, we
push a `(dst_dir_fd, src_statx)` frame.  fsiter's `dir_stack` semantics
are asymmetric:

- For files/symlinks, `dir_stack` is the chain of ancestors only.
- For directories, `dir_stack` includes the directory itself (already
  pushed for upcoming descent or skip).

The runner pops frames whose corresponding `dir_stack` entry has changed
or disappeared — this catches sibling-directory transitions where the
length stays the same but the last entry differs.

---

## Kernel compatibility

- `STATMOUNT_SB_SOURCE` (kernel 6.18+) is used to validate the fsiter
  source name when available; on older kernels we fall back to the
  mountpoint string and fsiter's strict source-name check is also
  disabled, so the fallback is benign.
- All other features (`statmount`, `openat2`, `RESOLVE_NO_SYMLINKS`,
  `statx`, `iter_filesystem_contents`) require kernel 6.8+.

## Tests

- `tests/utils/test_shutil_copy.py` — file-level primitives.
- `tests/utils/test_shutil_copytree.py` — tree-level operations,
  including `CopyFlags`, `CopyTreeOp`, `CopyJob`, `exist_ok`, and a
  ZFS-gated `.zfs` ctldir test.
- `tests/type_checks/test_shutil_types.py` — `assert_type`-based static
  typing pins for the public surface.
