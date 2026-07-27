// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_ANCHOR_H
#define _URING_ANCHOR_H

#include <Python.h>

/*
 * An Anchor is a long-lived *real* directory fd that every path-resolving
 * operation resolves against.
 *
 * Real, not a fixed-table index, by kernel constraint: every io_uring path
 * op's prep resolves its dirfd through the normal fd table and rejects a
 * fixed-table dirfd with -EBADF.
 */
typedef struct {
	PyObject_HEAD
	int fd;
	PyObject *path;		/* bytes, for repr/errors; may be NULL */
} AnchorObject;

extern PyTypeObject AnchorType;

#define Anchor_Check(op) PyObject_TypeCheck(op, &AnchorType)

/*
 * Validate a single path component ("leaf").
 *
 * This is confinement, not hygiene: the plain *at opcodes (RENAMEAT,
 * UNLINKAT, MKDIRAT, SYMLINKAT, LINKAT) honour no RESOLVE_* flags whatsoever,
 * so a multi-component or ".." name would walk wherever it pleased. Only
 * OPENAT2 can be confined in-kernel, via RESOLVE_BENEATH.
 *
 * On success returns 0, sets *out to a NUL-terminated buffer owned by
 * *owner (a new reference the caller must release), and sets *len.
 * On failure returns -1 with ValueError set.
 */
int uring_leaf_convert(PyObject *obj, PyObject **owner, const char **out,
		       Py_ssize_t *len);

int init_anchor_type(PyObject *module);

#endif /* _URING_ANCHOR_H */
