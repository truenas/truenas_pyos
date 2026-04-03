# CLAUDE.md

## Project layout

| Path | Contents |
|---|---|
| `src/cext/os/` | `truenas_os` C extension (mount, ACL, statx, fsiter, …) |
| `src/cext/filter_utils/` | `truenas_pyfilter` C extension (compiled filter engine) |
| `src/truenas_os_pyutils/` | Pure-Python utilities built on `truenas_os` |
| `stubs/truenas_os/` | Type stubs for `truenas_os` |
| `stubs/truenas_pyfilter/` | Type stubs for `truenas_pyfilter` |
| `tests/` | pytest suite |
| `tests/type_checks/` | mypy typing tests |
| `tests/.stubtest_allowlist.txt` | stubtest allowlist for `truenas_os` |

## Build & install

```bash
dpkg-buildpackage -us -uc -b
dpkg -i ../python3-truenas-pyos_*.deb
```

## Running tests

```bash
python3 -m pytest tests/ -v --tb=short
```

## Stub validation (required after any `.pyi` change)

Run all checks before declaring stub work done:

```bash
python3 -m mypy stubs/truenas_os/
python3 -m mypy stubs/truenas_pyfilter/
python3 -m mypy src/truenas_os_pyutils/
python3 -m mypy tests/type_checks/

python3 -c "
from mypy.stubtest import main; import sys
sys.argv = ['stubtest', 'truenas_os', '--allowlist', 'tests/.stubtest_allowlist.txt']
sys.exit(main())
"
python3 -c "
from mypy.stubtest import main; import sys
sys.argv = ['stubtest', 'truenas_pyfilter']
sys.exit(main())
"
```

The full sequence above is what CI runs in `qemu-4-test.sh`.

## C code style

- Single space after type name in declarations — no extra spaces for alignment.
  - Write `PyObject **keys;`, not `PyObject  **keys;`
  - This applies to struct fields, local variables, and function parameters.
- Declare all local variables at the top of the function (C89 style); do not declare variables in the middle of a block.
- NULL-initialize all pointer variables at declaration (e.g. `PyObject *result = NULL;`).
- Prefer the PyUnicode API (`PyUnicode_*`) over C string APIs (`strcmp`, `strtol` on UTF-8 bytes, etc.) when working with Python string objects.
- Use `PyMem_RawMalloc` / `PyMem_RawCalloc` / `PyMem_RawFree` for manual memory allocation; do not use `malloc` / `calloc` / `free`.

## C extension reference counting

- Use `Py_CLEAR(x)` instead of `Py_DECREF(x); x = NULL;` — they are equivalent but `Py_CLEAR` is one line and avoids the dangling pointer window.
- Use `Py_SETREF(x, y)` / `Py_XSETREF(x, y)` instead of `Py_DECREF(x); x = y;` / `Py_XDECREF(x); x = y;` when reassigning an owned reference to a new value. `Py_SETREF` asserts the old value is non-NULL; use the `X` variant when it may be NULL.
- Do not embed side-effectful assignments (`tmp = expr`) inside function call argument lists. Assign to the temporary first, then pass it to the function, then use `Py_XSETREF` for subsequent reuse of the same temporary.
- Always NULL-check the result of object-creating calls (`PyLong_From*`, `PyUnicode_From*`, etc.) before passing the result to any other function. Passing NULL into a C-API call is undefined behaviour — CPython asserts `arg != NULL` in debug builds and will crash or corrupt state in release builds. An aggregated check (`if (!a || !b || !c)`) after all the allocations is acceptable only when none of the allocated values are passed to another function before the check.
- Never call `Py_DECREF` / `Py_XDECREF` on borrowed references (`PyList_GET_ITEM`, `PyTuple_GET_ITEM`, `PySequence_Fast_GET_ITEM`, dict values from `PyDict_GetItem`, etc.).
- In `tp_dealloc`, clear all owned `PyObject *` fields with `Py_CLEAR` before calling `tp_free`.

## Kernel / ZFS version compatibility

All C source changes must be consistent with the TrueNAS kernel and ZFS versions.
- TrueNAS kernel source: https://github.com/truenas/linux
- TrueNAS ZFS source: https://github.com/truenas/zfs
- If the correct kernel or ZFS version is unknown, ask the user for their local paths before making any functional changes.
- Do **not** silently adapt code to a different kernel version.
- When a C extension API changes, check whether `src/truenas_os_pyutils/` or other higher-level modules in this repo also need to be updated.

## README files

Each module has its own README next to its source:

| Module | README |
|---|---|
| `truenas_os` | `src/cext/os/README.md` |
| `truenas_pyfilter` | `src/cext/filter_utils/README.md` |
| `truenas_os_pyutils` | `src/truenas_os_pyutils/README.md` |

The repo-level `README.md` is a brief overview with links; detailed API reference lives in the per-module files.

When making code changes, update the relevant README(s) to reflect them — new functions, changed signatures, removed features, new constants, changed behavior. Do not leave documentation out of sync with the implementation.

## Stubs vs source changes

When working on `.pyi` type stubs:
- Do **not** change the corresponding C source unless the user explicitly requests it or an obvious bug is present.
- Stubs describe the existing API; they should not drive source changes.
