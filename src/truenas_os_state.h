// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef TRUENAS_OS_STATE_H
#define TRUENAS_OS_STATE_H

#include <Python.h>

/* Module state - stores PyStructSequence types */
typedef struct {
	PyObject *StatxResultType;
	PyObject *StatmountResultType;
	PyObject *IterInstanceType;
	PyObject *FilesystemIterStateType;
} truenas_os_state_t;

/* Get module state */
truenas_os_state_t *get_truenas_os_state(PyObject *module);

#endif /* TRUENAS_OS_STATE_H */
