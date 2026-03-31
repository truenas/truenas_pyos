// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _PY_STATX_H
#define _PY_STATX_H

#include <Python.h>
#include <stdint.h>

// Core statx function
PyObject *do_statx(int dirfd, const char *pathname, int flags, unsigned int mask);

// Convert struct statx to Python StatxResult
PyObject *statx_to_pyobject(const struct statx *stx);

// C wrapper for statx() - returns 0 on success, -1 with errno set on error
int statx_impl(int dirfd, const char *pathname, int flags, unsigned int mask, struct statx *stx);

// Initialize statx types (StatxResult) and constants
int init_statx_types(PyObject *module);

#endif /* _PY_STATX_H */
