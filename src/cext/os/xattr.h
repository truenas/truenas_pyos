// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _XATTR_H_
#define _XATTR_H_

#include <Python.h>
#include <stddef.h>

/*
 * TRUENAS_XATTR_SIZE_MAX - upper bound on xattr value / name-list size
 * we will allocate for.  TrueNAS allows up to 2 MiB xattrs.  fsetxattr
 * short-circuits with OSError(E2BIG) for larger values; the read path
 * refuses to allocate past this cap and reports E2BIG if the probe
 * returns a larger size.
 */
#define TRUENAS_XATTR_SIZE_MAX (2 * 1024 * 1024)

/*
 * do_fgetxattr - read xattr `name` from open `fd`.
 *
 * Uses a 512-byte stack buffer first; on ERANGE escalates to a
 * heap buffer that doubles up to TRUENAS_XATTR_SIZE_MAX.
 *
 * Returns a PyBytes object on success, or NULL with the Python error
 * indicator set on failure.
 */
PyObject *do_fgetxattr(int fd, const char *name);

/*
 * do_fsetxattr - write `value` (length `value_len`) as xattr `name` on
 * open `fd`.  `flags` matches the Linux fsetxattr(2) flags argument
 * (XATTR_CREATE, XATTR_REPLACE, or 0).
 *
 * Validates `value_len` against TRUENAS_XATTR_SIZE_MAX before issuing
 * the syscall and raises OSError(E2BIG) if exceeded.
 *
 * Returns 0 on success, -1 on failure with the Python error indicator set.
 */
int do_fsetxattr(int fd, const char *name,
                 const char *value, size_t value_len, int flags);

/*
 * do_flistxattr - return the list of xattr names on open `fd`.
 *
 * Same buffer-growth strategy as do_fgetxattr.  Parses the kernel's
 * NUL-separated name list into a Python list of str.
 *
 * Returns a PyList of str on success, or NULL with the Python error
 * indicator set on failure.
 */
PyObject *do_flistxattr(int fd);

/*
 * init_xattr_constants - export XATTR_CREATE, XATTR_REPLACE, and
 * XATTR_SIZE_MAX module-level int constants on `module`.
 *
 * Returns 0 on success, -1 on failure.
 */
int init_xattr_constants(PyObject *module);

#endif /* _XATTR_H_ */
