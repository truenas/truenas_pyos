// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _PY_STATX_H
#define _PY_STATX_H

#include <Python.h>
#include <stdint.h>

// Core statx function
PyObject *do_statx(int dirfd, const char *pathname, int flags, unsigned int mask);

// Initialize statx types (StatxResult) and constants
int init_statx_types(PyObject *module);

#endif /* _PY_STATX_H */
