// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_OPS_H
#define _URING_OPS_H

#include <Python.h>

/*
 * Reactor operation methods. Defined in ops.c, listed in reactor.c's single
 * tp_methods table -- one static array, resolved at link time.
 */
PyObject *Reactor_open(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *Reactor_pread(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *Reactor_pwrite(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *Reactor_fsync(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *Reactor_statx(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *Reactor_close_file(PyObject *self, PyObject *file_obj);

extern const char Reactor_open__doc__[];
extern const char Reactor_pread__doc__[];
extern const char Reactor_pwrite__doc__[];
extern const char Reactor_fsync__doc__[];
extern const char Reactor_statx__doc__[];
extern const char Reactor_close_file__doc__[];

#endif /* _URING_OPS_H */
