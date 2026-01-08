# truenas_pyos

Python bindings for modern Linux filesystem and mount syscalls.

This module provides Python access to Linux-specific system calls that are not available in the standard library, with a focus on mount management, extended file operations, and file handle support.

## Features

- **Mount Management**: List, query, and iterate over filesystem mounts
- **Extended File Operations**: Advanced file opening with path resolution control
- **File Handles**: Create and use filesystem-independent file handles
- **Extended Stat**: Get detailed file metadata including creation time and mount IDs

## Installation

```bash
python3 -m pip install .
```

Or for development:

```bash
python3 -m pip install -e .
```

## Requirements

- Python 3.13+
- Linux kernel 6.18+ (for full feature support)
- GCC compiler
- libbsd-dev

## API Reference

### Mount Operations

#### `listmount(mnt_id=LSMT_ROOT, last_mnt_id=0, reverse=False)`

List mount IDs under a given mount point.

```python
import truenas_os

# Get all mount IDs from root
mount_ids = truenas_os.listmount()
print(f"Found {len(mount_ids)} mounts")
```

**Parameters:**
- `mnt_id` (int): Mount ID to list children of (default: `LSMT_ROOT`)
- `last_mnt_id` (int): For pagination (default: 0)
- `reverse` (bool): List in reverse order (default: False)

**Returns:** List of mount IDs (integers)

---

#### `statmount(mnt_id, mask=STATMOUNT_MNT_BASIC|STATMOUNT_SB_BASIC)`

Get detailed information about a mount.

```python
import truenas_os

mounts = truenas_os.listmount()
info = truenas_os.statmount(
    mounts[0],
    mask=truenas_os.STATMOUNT_MNT_BASIC |
         truenas_os.STATMOUNT_MNT_POINT |
         truenas_os.STATMOUNT_FS_TYPE
)
print(f"Mount point: {info.mnt_point}")
print(f"Filesystem: {info.fs_type}")
```

**Parameters:**
- `mnt_id` (int): Mount ID to query
- `mask` (int): Fields to retrieve (STATMOUNT_* constants)

**Returns:** `StatmountResult` named tuple with fields:
- `mnt_id`, `mnt_parent_id`, `mnt_id_old`, `mnt_parent_id_old`
- `mnt_root`, `mnt_point`, `mnt_attr`, `mnt_propagation`
- `mnt_peer_group`, `mnt_master`, `propagate_from`
- `fs_type`, `mnt_ns_id`, `mnt_opts`
- `sb_dev_major`, `sb_dev_minor`, `sb_magic`, `sb_flags`
- `mask`

**STATMOUNT_* Constants:**
- `STATMOUNT_SB_BASIC` - Basic superblock info
- `STATMOUNT_MNT_BASIC` - Basic mount info
- `STATMOUNT_PROPAGATE_FROM` - Propagation source
- `STATMOUNT_MNT_ROOT` - Mount root path
- `STATMOUNT_MNT_POINT` - Mount point path
- `STATMOUNT_FS_TYPE` - Filesystem type
- `STATMOUNT_MNT_NS_ID` - Mount namespace ID
- `STATMOUNT_MNT_OPTS` - Mount options

---

#### `iter_mount(mnt_id=LSMT_ROOT, last_mnt_id=0, reverse=False, statmount_flags=...)`

Efficiently iterate over mounts, yielding StatmountResult objects.

```python
import truenas_os

# Iterate over all mounts
flags = (truenas_os.STATMOUNT_MNT_BASIC |
         truenas_os.STATMOUNT_MNT_POINT |
         truenas_os.STATMOUNT_FS_TYPE)

for mount_info in truenas_os.iter_mount(statmount_flags=flags):
    print(f"{mount_info.mnt_point}: {mount_info.fs_type}")
```

**Parameters:**
- `mnt_id` (int): Mount ID to list children of
- `last_mnt_id` (int): For pagination
- `reverse` (bool): Reverse order
- `statmount_flags` (int): Fields to retrieve for each mount

**Returns:** Iterator yielding `StatmountResult` objects

---

#### `open_mount_by_id(mount_id, flags=os.O_DIRECTORY)`

Open a file descriptor for a mount point by its mount ID.

```python
import truenas_os
import os

mounts = truenas_os.listmount()
fd = truenas_os.open_mount_by_id(mounts[0], os.O_RDONLY | os.O_DIRECTORY)
# Use fd...
os.close(fd)
```

**Parameters:**
- `mount_id` (int): Mount ID to open
- `flags` (int): Open flags (default: `O_DIRECTORY`)

**Returns:** File descriptor (int)

---

#### `move_mount(from_dirfd, from_pathname, to_dirfd, to_pathname, flags=0)`

Move a mount from one location to another.

```python
import truenas_os

truenas_os.move_mount(
    truenas_os.AT_FDCWD, "/old/path",
    truenas_os.AT_FDCWD, "/new/path"
)
```

**Parameters:**
- `from_dirfd` (int): Source directory fd (or `AT_FDCWD`)
- `from_pathname` (str): Source path
- `to_dirfd` (int): Destination directory fd (or `AT_FDCWD`)
- `to_pathname` (str): Destination path
- `flags` (int): Movement flags (`MOVE_MOUNT_`* constants)

**MOVE_MOUNT_* Constants:**
- `MOVE_MOUNT_F_SYMLINKS` - Follow symlinks on from path
- `MOVE_MOUNT_F_AUTOMOUNTS` - Follow automounts on from path
- `MOVE_MOUNT_F_EMPTY_PATH` - Empty from path permitted
- `MOVE_MOUNT_T_SYMLINKS` - Follow symlinks on to path
- `MOVE_MOUNT_T_AUTOMOUNTS` - Follow automounts on to path
- `MOVE_MOUNT_T_EMPTY_PATH` - Empty to path permitted
- `MOVE_MOUNT_SET_GROUP` - Set sharing group instead
- `MOVE_MOUNT_BENEATH` - Mount beneath top mount

---

### Extended File Operations

#### `openat2(dirfd, pathname, flags, mode=0, resolve=0)`

Open a file with enhanced path resolution control.

```python
import truenas_os
import os

# Open file with symlink blocking
fd = truenas_os.openat2(
    truenas_os.AT_FDCWD,
    "/path/to/file",
    os.O_RDONLY,
    resolve=truenas_os.RESOLVE_NO_SYMLINKS
)
os.close(fd)

# Prevent directory escaping
tmpdir_fd = os.open("/tmp", os.O_RDONLY | os.O_DIRECTORY)
try:
    fd = truenas_os.openat2(
        tmpdir_fd,
        "subdir/file",
        os.O_RDONLY,
        resolve=truenas_os.RESOLVE_BENEATH
    )
    os.close(fd)
finally:
    os.close(tmpdir_fd)
```

**Parameters:**
- `dirfd` (int): Directory fd (or `AT_FDCWD`)
- `pathname` (str): Path to open
- `flags` (int): Open flags (O_* constants from os module)
- `mode` (int): File permissions for `O_CREAT`/`O_TMPFILE`
- `resolve` (int): Path resolution flags (`RESOLVE_`* constants)

**Returns:** File descriptor (int)

**RESOLVE_* Constants:**
- `RESOLVE_NO_XDEV` - Block mount-point crossings
- `RESOLVE_NO_MAGICLINKS` - Block procfs magic-links
- `RESOLVE_NO_SYMLINKS` - Block all symlinks
- `RESOLVE_BENEATH` - Block escaping dirfd
- `RESOLVE_IN_ROOT` - Scope paths inside dirfd
- `RESOLVE_CACHED` - Only use cached lookup

---

#### `statx(dirfd, pathname, flags=0, mask=STATX_BASIC_STATS|STATX_BTIME)`

Get extended file attributes.

```python
import truenas_os

result = truenas_os.statx(
    truenas_os.AT_FDCWD,
    "/path/to/file",
    mask=truenas_os.STATX_BASIC_STATS | truenas_os.STATX_BTIME
)

print(f"Size: {result.stx_size}")
print(f"Birth time: {result.stx_btime}")
print(f"Mount ID: {result.stx_mnt_id}")
```

**Parameters:**
- `dirfd` (int): Directory fd (or `AT_FDCWD`)
- `pathname` (str): Path to file
- `flags` (int): `AT_`* flags
- `mask` (int): Fields to retrieve (`STATX_`* constants)

**Returns:** `StatxResult` named tuple with extensive file metadata

**Key STATX_* Constants:**
- `STATX_BASIC_STATS` - All basic stat info
- `STATX_BTIME` - File creation time
- `STATX_MNT_ID` - Mount ID
- `STATX_MNT_ID_UNIQUE` - Unique mount ID
- `STATX_DIOALIGN` - Direct I/O alignment info

---

### File Handle Operations

#### `fhandle(path=None, dir_fd=AT_FDCWD, flags=0, handle_bytes=None, mount_id=None)`

Create or restore a file handle.

```python
import truenas_os

# Create file handle from path
fh = truenas_os.fhandle(path="/path/to/file")
print(f"Mount ID: {fh.mount_id}")

# Serialize to bytes
handle_bytes = bytes(fh)

# Restore from bytes
fh2 = truenas_os.fhandle(handle_bytes=handle_bytes, mount_id=fh.mount_id)

# Open file from handle
import os
mount_fd = os.open("/", os.O_RDONLY)
fd = fh2.open(mount_fd=mount_fd, flags=os.O_RDONLY)
# Use fd...
os.close(fd)
os.close(mount_fd)
```

**Methods:**
- `open(mount_fd, flags=0)` - Open file descriptor from handle
- `__bytes__()` - Serialize handle to bytes

**Properties:**
- `mount_id` - Mount ID where handle was created

**FH_AT_* Constants:**
- `FH_AT_SYMLINK_FOLLOW` - Follow symbolic links
- `FH_AT_EMPTY_PATH` - Allow empty path with fd
- `FH_AT_HANDLE_FID` - Return file identifier handle
- `FH_AT_HANDLE_CONNECTABLE` - Return connectable handle

---

## Complete Example

```python
import truenas_os
import os

# List and inspect all mounts
print("=== Filesystem Mounts ===")
flags = (truenas_os.STATMOUNT_MNT_BASIC |
         truenas_os.STATMOUNT_MNT_POINT |
         truenas_os.STATMOUNT_FS_TYPE)

for mount_info in truenas_os.iter_mount(statmount_flags=flags):
    if mount_info.mnt_point and mount_info.fs_type:
        print(f"{mount_info.mnt_point:<30} {mount_info.fs_type}")

# Get detailed file information
print("\n=== File Statistics ===")
info = truenas_os.statx(
    truenas_os.AT_FDCWD,
    "/etc/hosts",
    mask=truenas_os.STATX_BASIC_STATS | truenas_os.STATX_BTIME
)
print(f"Size: {info.stx_size} bytes")
print(f"Created: {info.stx_btime}")
print(f"Mount ID: {info.stx_mnt_id}")

# Safe file opening with path resolution control
print("\n=== Safe File Opening ===")
try:
    fd = truenas_os.openat2(
        truenas_os.AT_FDCWD,
        "/etc/hosts",
        os.O_RDONLY,
        resolve=truenas_os.RESOLVE_NO_SYMLINKS
    )
    data = os.read(fd, 100)
    print(f"Read {len(data)} bytes")
    os.close(fd)
except OSError as e:
    print(f"Failed: {e}")

# File handle operations
print("\n=== File Handle ===")
fh = truenas_os.fhandle(path="/etc/hosts")
print(f"Created file handle for mount_id={fh.mount_id}")
handle_bytes = bytes(fh)
print(f"Serialized to {len(handle_bytes)} bytes")
```

## License

LGPL-3.0-or-later

## Contributing

Contributions are welcome! Please ensure:
- All tests pass: `python3 -m pytest tests/`
- Code follows existing patterns
- New features include tests
- SPDX license identifiers are present
- **Type stubs are kept up-to-date**: When modifying the C extension API (adding/removing functions, changing signatures, or modifying return types), update `truenas_os.pyi` accordingly to maintain accurate type information

## Authors

- Andrew Walker (original author)
- TrueNAS contributors

## See Also

- `mount(2)`, `statmount(2)`, `listmount(2)` - Mount operations
- `openat2(2)` - Extended open with path resolution
- `statx(2)` - Extended file status
- `name_to_handle_at(2)`, `open_by_handle_at(2)` - File handle operations
- `move_mount(2)` - Move mount operations
