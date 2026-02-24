# truenas_pyos

Python bindings for modern Linux filesystem and mount syscalls.

This module provides Python access to Linux-specific system calls that are not available in the standard library, with a focus on mount management, extended file operations, and file handle support.

## Features

- **Mount Management**: List, query, and iterate over filesystem mounts
- **Filesystem Context API**: Modern mount API using fsopen/fsconfig/fsmount
- **Extended File Operations**: Advanced file opening with path resolution control
- **File Handles**: Create and use filesystem-independent file handles
- **Extended Stat**: Get detailed file metadata including creation time and mount IDs
- **Filesystem Iteration**: Secure recursive iteration over filesystem contents
- **ACL Support**: Read and write NFS4 and POSIX1E access control lists via file descriptor

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

#### `mount_setattr(*, path, attr_set=0, attr_clr=0, propagation=0, userns_fd=0, dirfd=AT_FDCWD, flags=0)`

Change properties of a mount or mount tree.

```python
import truenas_os

# Make a mount read-only
truenas_os.mount_setattr(
    path='/mnt/data',
    attr_set=truenas_os.MOUNT_ATTR_RDONLY
)

# Make entire mount tree read-only recursively
truenas_os.mount_setattr(
    path='/mnt/data',
    attr_set=truenas_os.MOUNT_ATTR_RDONLY,
    flags=truenas_os.AT_RECURSIVE
)
```

**Parameters (all keyword-only):**
- `path` (str): Path to the mount point
- `attr_set` (int): Mount attributes to set (MOUNT_ATTR_* constants)
- `attr_clr` (int): Mount attributes to clear
- `propagation` (int): Mount propagation type (MS_SHARED, MS_SLAVE, etc.)
- `userns_fd` (int): User namespace fd for MOUNT_ATTR_IDMAP
- `dirfd` (int): Directory fd (default: AT_FDCWD)
- `flags` (int): Flags like AT_RECURSIVE

**MOUNT_ATTR_* Constants:**
- `MOUNT_ATTR_RDONLY` - Make mount read-only
- `MOUNT_ATTR_NOSUID` - Ignore suid/sgid bits
- `MOUNT_ATTR_NODEV` - Disallow device access
- `MOUNT_ATTR_NOEXEC` - Disallow program execution
- `MOUNT_ATTR_RELATIME` - Update atime relatively
- `MOUNT_ATTR_NOATIME` - Do not update access times
- `MOUNT_ATTR_STRICTATIME` - Always update atime
- `MOUNT_ATTR_NODIRATIME` - Do not update directory access times

---

### Filesystem Context Operations

The filesystem context API (fsopen/fsconfig/fsmount) provides a modern, programmatic way to create and configure filesystems before mounting them. This API separates filesystem creation from mount point attachment, allowing fine-grained control over mount options.

#### `fsopen(*, fs_name, flags=0)`

Open a filesystem context for configuration.

```python
import truenas_os

# Create a filesystem context for tmpfs
fs_fd = truenas_os.fsopen(
    fs_name='tmpfs',
    flags=truenas_os.FSOPEN_CLOEXEC
)
```

**Parameters (all keyword-only):**
- `fs_name` (str): Filesystem type (e.g., 'ext4', 'xfs', 'tmpfs', 'btrfs')
- `flags` (int): Control flags (default: 0)

**Returns:** File descriptor for the filesystem context

**FSOPEN_* Constants:**
- `FSOPEN_CLOEXEC` - Set close-on-exec flag

---

#### `fsconfig(*, fs_fd, cmd, key=None, value=None, aux=0)`

Configure a filesystem context.

```python
import truenas_os

fs_fd = truenas_os.fsopen(fs_name='tmpfs', flags=truenas_os.FSOPEN_CLOEXEC)

# Set filesystem size
truenas_os.fsconfig(
    fs_fd=fs_fd,
    cmd=truenas_os.FSCONFIG_SET_STRING,
    key='size',
    value='1G'
)

# Set another option
truenas_os.fsconfig(
    fs_fd=fs_fd,
    cmd=truenas_os.FSCONFIG_SET_STRING,
    key='mode',
    value='0755'
)

# Create the filesystem
truenas_os.fsconfig(
    fs_fd=fs_fd,
    cmd=truenas_os.FSCONFIG_CMD_CREATE
)
```

**Parameters (all keyword-only):**
- `fs_fd` (int): File descriptor from fsopen()
- `cmd` (int): Configuration command (FSCONFIG_* constant)
- `key` (str): Option name (for SET_* commands)
- `value` (str|bytes|int): Option value
- `aux` (int): Auxiliary parameter (for FSCONFIG_SET_FD)

**Returns:** None

**FSCONFIG_* Commands:**
- `FSCONFIG_SET_FLAG` - Set a flag option (key only, no value)
- `FSCONFIG_SET_STRING` - Set a string-valued option
- `FSCONFIG_SET_BINARY` - Set a binary blob option
- `FSCONFIG_SET_PATH` - Set an option from a file path
- `FSCONFIG_SET_PATH_EMPTY` - Set from an empty path
- `FSCONFIG_SET_FD` - Set from a file descriptor
- `FSCONFIG_CMD_CREATE` - Create the filesystem (call after configuration)
- `FSCONFIG_CMD_RECONFIGURE` - Reconfigure an existing filesystem

---

#### `fsmount(*, fs_fd, flags=0, attr_flags=0)`

Create a mount object from a configured filesystem context.

```python
import truenas_os
import os

# Create and configure filesystem
fs_fd = truenas_os.fsopen(fs_name='tmpfs', flags=truenas_os.FSOPEN_CLOEXEC)
truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_SET_STRING,
                    key='size', value='512M')
truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_CMD_CREATE)

# Create mount object with read-only attribute
mnt_fd = truenas_os.fsmount(
    fs_fd=fs_fd,
    flags=truenas_os.FSMOUNT_CLOEXEC,
    attr_flags=truenas_os.MOUNT_ATTR_RDONLY
)
os.close(fs_fd)

# Attach to filesystem tree
truenas_os.move_mount(
    from_path='',
    to_path='/mnt/mytmpfs',
    from_dirfd=mnt_fd,
    flags=truenas_os.MOVE_MOUNT_F_EMPTY_PATH
)
os.close(mnt_fd)
```

**Parameters (all keyword-only):**
- `fs_fd` (int): File descriptor from fsopen() (after configuration)
- `flags` (int): Mount flags (FSMOUNT_* constants)
- `attr_flags` (int): Mount attributes (MOUNT_ATTR_* constants)

**Returns:** File descriptor for the mount object

**FSMOUNT_* Constants:**
- `FSMOUNT_CLOEXEC` - Set close-on-exec flag

**Complete Example:**

```python
import truenas_os
import os

# 1. Create filesystem context
fs_fd = truenas_os.fsopen(fs_name='tmpfs', flags=truenas_os.FSOPEN_CLOEXEC)

# 2. Configure filesystem options
truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_SET_STRING,
                    key='size', value='100M')
truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_SET_STRING,
                    key='nr_inodes', value='10k')

# 3. Create the filesystem
truenas_os.fsconfig(fs_fd=fs_fd, cmd=truenas_os.FSCONFIG_CMD_CREATE)

# 4. Create mount object with attributes
mnt_fd = truenas_os.fsmount(
    fs_fd=fs_fd,
    flags=truenas_os.FSMOUNT_CLOEXEC,
    attr_flags=truenas_os.MOUNT_ATTR_NOSUID | truenas_os.MOUNT_ATTR_NODEV
)
os.close(fs_fd)

# 5. Attach mount to the filesystem tree
truenas_os.move_mount(
    from_path='',
    to_path='/mnt/secure_tmp',
    from_dirfd=mnt_fd,
    flags=truenas_os.MOVE_MOUNT_F_EMPTY_PATH
)
os.close(mnt_fd)

print("Filesystem mounted at /mnt/secure_tmp")
```

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

### Filesystem Iteration

#### `iter_filesystem_contents(mountpoint, filesystem_name, /, *, relative_path=None, btime_cutoff=0,`
#### `file_open_flags=os.O_RDONLY, reporting_increment=1000, reporting_callback=None, reporting_private_data=None, dir_stack=None)`

Depth-first iteration over filesystem contents.

```python
import truenas_os
import os

for item in truenas_os.iter_filesystem_contents("/mnt/tank", "tank/dataset"):
    full_path = os.path.join(item.parent, item.name)
    print(f"{full_path}: {item.statxinfo.stx_size} bytes")
    # Do NOT close item.fd - iterator manages fd lifecycle
```

**Parameters:**
- `mountpoint` (str): Mount point path
- `filesystem_name` (str): Filesystem source to verify
- `relative_path` (str|None): Subdirectory within mountpoint (default: None)
- `btime_cutoff` (int): Skip files with btime > this value (default: 0)
- `file_open_flags` (int): Flags for opening files (default: O_RDONLY)
- `reporting_increment` (int): Callback interval in items (default: 1000)
- `reporting_callback` (callable|None): Function(dir_stack, FilesystemIterState, private_data) (default: None)
- `reporting_private_data` (any): User data for callback (default: None)
- `dir_stack` (tuple|None): Tuple of (path, inode) tuples from a previous iterator to restore directory path (default: None)

**Returns:** FilesystemIterator yielding IterInstance objects

**IterInstance:**
- `parent` (str): Directory path
- `name` (str): Entry name
- `fd` (int): Open file descriptor (valid until next iteration, do not close - iterator manages lifecycle)
- `statxinfo` (StatxResult): File metadata
- `isdir` (bool): True if directory, False otherwise
- `islnk` (bool): True if symlink, False otherwise
- `isreg` (bool): True if regular file, False otherwise

**FilesystemIterator.get_stats() returns FilesystemIterState:**
- `cnt` (int): Items yielded
- `cnt_bytes` (int): Bytes processed
- `current_directory` (str): Current directory path

**FilesystemIterator.skip():**

Skip recursion into the currently yielded directory. Call this immediately after the iterator yields a directory to
prevent descending into it.

```python
import truenas_os

iterator = truenas_os.iter_filesystem_contents("/mnt/tank", "tank/dataset")
for item in iterator:
    if item.isdir and item.name == "skip_this":
        iterator.skip()  # Don't recurse into this directory
```

Raises `ValueError` if called on a non-directory item.

**FilesystemIterator.dir_stack():**

Returns the current directory stack as a tuple of `(path, inode)` tuples. The first element is the root directory, and
the last element is the current directory being processed. Returns an empty tuple if iteration has completed.

```python
import truenas_os

iterator = truenas_os.iter_filesystem_contents("/mnt/tank", "tank/dataset")
for item in iterator:
    stack = iterator.dir_stack()
    print(f"Current depth: {len(stack)}")
    for path, inode in stack:
        print(f"  {path} (inode: {inode})")
```

**Restoring Iterator Position with dir_stack:**

The `dir_stack` parameter enables resuming iteration from a previously saved directory path. When provided, the iterator
reconstructs the directory path by matching inode numbers, descending through the tree without yielding intermediate
directories.

```python
import truenas_os

# Initial iteration - save position
iterator1 = truenas_os.iter_filesystem_contents("/mnt/tank", "tank/dataset")
saved_stack = None

for item in iterator1:
    if some_condition:
        saved_stack = iterator1.dir_stack()
        break

# Later - restore from saved position
iterator2 = truenas_os.iter_filesystem_contents(
    "/mnt/tank",
    "tank/dataset",
    dir_stack=saved_stack
)

# Iteration continues from the saved directory
for item in iterator2:
    process(item)
```

**Restoration behavior:**
- The iterator descends through the saved directory path without yielding intermediate directories
- Once the target directory is reached, iteration proceeds from the beginning of that directory
- DIR* streams cannot seek to specific positions within a directory, so files within the restored directory may be
  re-yielded if they were already processed
- If a directory in the saved path no longer exists or has a different inode, `IteratorRestoreError` is raised with
  the depth where restoration failed
- API consumers must implement their own logic to track which files within the restored directory have been processed
  if exact resume behavior is required

**Exception:**
- `IteratorRestoreError`: Raised when the saved directory path cannot be restored. The exception includes:
  - `depth` attribute: The directory stack depth (0-indexed) where restoration failed
  - `path` attribute: The directory path where the expected subdirectory was not found
  - Error message includes both the depth and path for easy debugging

---

### ACL Operations

The ACL API operates on open file descriptors. `fgetacl` probes the filesystem
type automatically and returns either an `NFS4ACL` or `POSIXACL` object.

#### `fgetacl(fd)`

Read the ACL from an open file descriptor.

```python
import truenas_os, os

fd = os.open("/mnt/tank/file", os.O_RDONLY)
acl = truenas_os.fgetacl(fd)
os.close(fd)

if isinstance(acl, truenas_os.NFS4ACL):
    for ace in acl.aces:
        print(ace)
```

**Parameters:**
- `fd` (int): Open file descriptor

**Returns:** `NFS4ACL` on NFS4/ZFS filesystems, `POSIXACL` on POSIX1E filesystems

**Raises:** `OSError(EOPNOTSUPP)` if ACLs are disabled on the filesystem

---

#### `fsetacl(fd, acl)`

Write an ACL to an open file descriptor. The ACL type must match the filesystem.

```python
import truenas_os, os

fd = os.open("/mnt/tank/file", os.O_RDONLY)
acl = truenas_os.fgetacl(fd)
# modify acl ...
truenas_os.fsetacl(fd, acl)
os.close(fd)
```

**Parameters:**
- `fd` (int): Open file descriptor
- `acl` (`NFS4ACL` | `POSIXACL`): ACL to write

**Raises:** `OSError` on failure, `TypeError` if `acl` is neither `NFS4ACL` nor `POSIXACL`

---

#### `fsetacl_nfs4(fd, data)` / `fsetacl_posix(fd, access_bytes, default_bytes)`

Low-level interfaces that write raw xattr bytes directly, bypassing the
`NFS4ACL`/`POSIXACL` wrappers. Prefer `fsetacl` unless you are managing the
wire format yourself.

---

#### `NFS4ACL`

Wraps the `system.nfs4_acl_xdr` xattr (big-endian XDR encoding).

```python
import truenas_os

# Construct from ACE objects
ace = truenas_os.NFS4Ace(
    truenas_os.NFS4AceType.ALLOW,
    truenas_os.NFS4Flag.FILE_INHERIT | truenas_os.NFS4Flag.DIRECTORY_INHERIT,
    truenas_os.NFS4Perm.READ_DATA | truenas_os.NFS4Perm.EXECUTE,
    truenas_os.NFS4Who.EVERYONE,
)
acl = truenas_os.NFS4ACL.from_aces([ace])

# Inspect
print(acl.acl_flags)   # NFS4ACLFlag
print(acl.trivial)     # True if ACL_IS_TRIVIAL is set
for ace in acl.aces:
    print(ace)

# Compute inherited ACL for a new child directory
child_acl = acl.generate_inherited_acl(is_dir=True)

# Round-trip through raw bytes
raw = bytes(acl)
acl2 = truenas_os.NFS4ACL(raw)
```

**Constructor:** `NFS4ACL(data: bytes)` — wrap raw XDR bytes

**Class method:**
- `from_aces(aces, acl_flags=NFS4ACLFlag(0))` — build from an iterable of `NFS4Ace` objects; ACEs are sorted into MS canonical order automatically

**Properties:**
- `acl_flags` (`NFS4ACLFlag`): ACL-level flags from the XDR header
- `aces` (`list[NFS4Ace]`): Parsed list of access control entries
- `trivial` (`bool`): True if `ACL_IS_TRIVIAL` is set (ACL is equivalent to mode bits)

**Methods:**
- `generate_inherited_acl(is_dir=False) -> NFS4ACL`: Apply NFS4 inheritance rules to produce the ACL for a new child object. File children include only `FILE_INHERIT` ACEs; directory children include `FILE_INHERIT` or `DIRECTORY_INHERIT` ACEs, with propagation flags adjusted according to `NO_PROPAGATE_INHERIT`. Raises `ValueError` if no ACEs would be inherited.
- `__bytes__() -> bytes`: Return the raw XDR bytes
- `__len__() -> int`: Number of ACEs

---

#### `NFS4Ace`

An individual NFS4 access control entry.

**Constructor:** `NFS4Ace(ace_type, ace_flags, access_mask, who_type, who_id=-1)`

**Properties:**
- `ace_type` (`NFS4AceType`): `ALLOW`, `DENY`, `AUDIT`, or `ALARM`
- `ace_flags` (`NFS4Flag`): Inheritance and audit flags
- `access_mask` (`NFS4Perm`): Permission bits
- `who_type` (`NFS4Who`): `NAMED`, `OWNER`, `GROUP`, or `EVERYONE`
- `who_id` (`int`): uid/gid for `NAMED` entries; `-1` for special principals

**NFS4AceType constants:** `ALLOW`, `DENY`, `AUDIT`, `ALARM`

**NFS4Who constants:** `NAMED`, `OWNER`, `GROUP`, `EVERYONE`

**NFS4Perm flags:** `READ_DATA`, `WRITE_DATA`, `APPEND_DATA`, `READ_NAMED_ATTRS`, `WRITE_NAMED_ATTRS`, `EXECUTE`, `DELETE_CHILD`, `READ_ATTRIBUTES`, `WRITE_ATTRIBUTES`, `DELETE`, `READ_ACL`, `WRITE_ACL`, `WRITE_OWNER`, `SYNCHRONIZE`

**NFS4Flag flags:** `FILE_INHERIT`, `DIRECTORY_INHERIT`, `NO_PROPAGATE_INHERIT`, `INHERIT_ONLY`, `SUCCESSFUL_ACCESS`, `FAILED_ACCESS`, `IDENTIFIER_GROUP`, `INHERITED`

**NFS4ACLFlag flags:** `AUTO_INHERIT`, `PROTECTED`, `DEFAULTED`

---

#### `POSIXACL`

Wraps the `system.posix_acl_access` and `system.posix_acl_default` xattrs
(little-endian Linux wire format).

```python
import truenas_os

# Construct from ACE objects
aces = [
    truenas_os.POSIXAce(truenas_os.POSIXTag.USER_OBJ,  truenas_os.POSIXPerm.READ | truenas_os.POSIXPerm.WRITE),
    truenas_os.POSIXAce(truenas_os.POSIXTag.GROUP_OBJ, truenas_os.POSIXPerm.READ),
    truenas_os.POSIXAce(truenas_os.POSIXTag.OTHER,     truenas_os.POSIXPerm(0)),
]
acl = truenas_os.POSIXACL.from_aces(aces)

# Inspect
print(acl.trivial)        # True if no named users/groups and no MASK entry
for ace in acl.aces:
    print(ace)
for ace in acl.default_aces:
    print(ace)

# Compute inherited ACL for a new child directory
child_acl = acl.generate_inherited_acl(is_dir=True)

# Raw bytes for each xattr
access_raw  = acl.access_bytes()
default_raw = acl.default_bytes()  # None if no default ACL
```

**Constructor:** `POSIXACL(access_data: bytes, default_data: bytes | None = None)` — wrap raw xattr bytes

**Class method:**
- `from_aces(aces)` — build from an iterable of `POSIXAce` objects; entries with `default=True` populate the default ACL

**Properties:**
- `aces` (`list[POSIXAce]`): Access ACL entries
- `default_aces` (`list[POSIXAce]`): Default ACL entries (empty if no default ACL)
- `trivial` (`bool`): True if the ACL contains only `USER_OBJ`, `GROUP_OBJ`, and `OTHER` entries without a `MASK` entry

**Methods:**
- `generate_inherited_acl(is_dir=True) -> POSIXACL`: Derive the ACL for a new child by promoting the default ACL. Raises `ValueError` if no default ACL is present.
- `access_bytes() -> bytes`: Raw access ACL xattr bytes
- `default_bytes() -> bytes | None`: Raw default ACL xattr bytes, or `None` if absent

---

#### `POSIXAce`

An individual POSIX ACL entry.

**Constructor:** `POSIXAce(tag, perms, id=-1, default=False)`

**Properties:**
- `tag` (`POSIXTag`): Entry type
- `perms` (`POSIXPerm`): Permission bits
- `id` (`int`): uid/gid for `USER`/`GROUP` entries; `-1` for special entries
- `default` (`bool`): True if this entry belongs to the default ACL

**POSIXTag constants:** `USER_OBJ`, `USER`, `GROUP_OBJ`, `GROUP`, `MASK`, `OTHER`

**POSIXPerm flags:** `READ`, `WRITE`, `EXECUTE`

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
- `fsopen(2)`, `fsconfig(2)`, `fsmount(2)` - Filesystem context operations
- `mount_setattr(2)` - Change mount attributes
- `move_mount(2)` - Move mount operations
- `openat2(2)` - Extended open with path resolution
- `statx(2)` - Extended file status
- `name_to_handle_at(2)`, `open_by_handle_at(2)` - File handle operations
