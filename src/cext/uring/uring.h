// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_H
#define _URING_H

#include <Python.h>

/*
 * Create the truenas_os.uring submodule and attach it to `parent`.
 * Returns 0 on success, -1 with an exception set.
 */
int init_uring_submodule(PyObject *parent);

#endif /* _URING_H */
