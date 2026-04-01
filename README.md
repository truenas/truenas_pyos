# truenas_pyos

Python bindings for modern Linux filesystem and mount syscalls, plus pure-Python
utilities built on top of them.

## Packages

### `truenas_os` (C extension)

Direct Python access to Linux syscalls not available in the standard library:
`statmount(2)`, `listmount(2)`, `openat2(2)`, `statx(2)`, `renameat2(2)`,
`mount_setattr(2)`, `move_mount(2)`, `fsmount(2)`/`fsopen(2)`/`fsconfig(2)`,
`name_to_handle_at(2)`/`open_by_handle_at(2)`, and NFS4/POSIX1E ACL xattr I/O.
See [`src/cext/os/README.md`](src/cext/os/README.md).

### `truenas_os_pyutils` (pure Python)

Higher-level utilities built on the C extension: symlink-safe file I/O
(`safe_open`, `atomic_write`, `atomic_replace`) and mount enumeration/unmounting
(`statmount`, `iter_mountinfo`, `umount`). See
[`src/truenas_os_pyutils/README.md`](src/truenas_os_pyutils/README.md).

### `truenas_pyfilter` (C extension)

High-performance compiled filter-list engine. Pre-compiles filter trees and
query options once so the inner iteration loop runs entirely in C with no
Python frame overhead.

```python
import truenas_pyfilter

filters = truenas_pyfilter.compile_filters([["name", "^", "z"]])
options = truenas_pyfilter.compile_options(order_by=["name"], limit=10)
results = truenas_pyfilter.tnfilter(records, filters=filters, options=options)
```

See [`src/cext/filter_utils/README.md`](src/cext/filter_utils/README.md).

## CLI Tools

### `truenas_getfacl`

Display NFS4 and POSIX1E ACL entries for files. Supports recursive traversal,
numeric IDs, JSONL output, and skipping trivial (mode-derived) ACLs.

### `truenas_setfacl`

Set NFS4 and POSIX1E ACL entries on files. Supports recursive traversal,
adding/removing/replacing entries, restoring from a `truenas_getfacl` backup,
an interactive curses editor, and targeting files by file handle.

See [`src/cext/os/README.md`](src/cext/os/README.md) for full usage.

## Installation

```bash
python3 -m pip install .
```

## Requirements

- Python 3.13+
- Linux kernel 6.8+ (`statmount(2)`, `listmount(2)`, `openat2(2)`)
- Linux kernel 6.18+ for `STATMOUNT_SB_SOURCE` (mount source field, ZFS snapshot detection)
- GCC
- libbsd-dev

## License

LGPL-3.0-or-later

## Contributing

- All tests pass: `python3 -m pytest tests/`
- SPDX license identifiers present on new files
- Type stubs kept in sync when a C extension API changes:
  - `truenas_os`: `stubs/truenas_os/__init__.pyi`
  - `truenas_pyfilter`: `stubs/truenas_pyfilter/__init__.pyi`
