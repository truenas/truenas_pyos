// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_PROBE_H
#define _URING_PROBE_H

#include <Python.h>
#include "truenas_os_state.h"

extern const char py_uring_query__doc__[];
extern const char py_uring_supported_ops__doc__[];

/*
 * Register the QueryResult struct sequence and the IORING_* constants.
 *
 * `module` is the truenas_os.uring submodule (where the names are added);
 * `state` is the *parent* module's state, passed explicitly because
 * PyState_FindModule() cannot resolve truenas_os until import fixup has run,
 * which is after PyInit_truenas_os() returns.
 *
 * Returns 0 on success, -1 with an exception set.
 */
int init_probe_types(PyObject *module, truenas_os_state_t *state);

/* truenas_os.uring.query() -> QueryResult | None */
PyObject *py_uring_query(PyObject *module, PyObject *ignored);

/* truenas_os.uring.supported_ops() -> frozenset[int] */
PyObject *py_uring_supported_ops(PyObject *module, PyObject *ignored);

#endif /* _URING_PROBE_H */
