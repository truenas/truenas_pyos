// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "fsmount.h"
#include <sys/syscall.h>
#include <unistd.h>
#include <linux/mount.h>

#define __NR_fsopen 430
#define __NR_fsconfig 431
#define __NR_fsmount 432

// fsopen flags
#ifndef FSOPEN_CLOEXEC
#define FSOPEN_CLOEXEC 0x00000001
#endif

// fsconfig commands
#ifndef FSCONFIG_SET_FLAG
#define FSCONFIG_SET_FLAG 0  /* Set parameter, supplying no value */
#define FSCONFIG_SET_STRING 1  /* Set parameter, supplying a string value */
#define FSCONFIG_SET_BINARY 2  /* Set parameter, supplying a binary blob value */
#define FSCONFIG_SET_PATH 3  /* Set parameter, supplying an object by path */
#define FSCONFIG_SET_PATH_EMPTY 4  /* Set parameter, supplying an object by (empty) path */
#define FSCONFIG_SET_FD 5  /* Set parameter, supplying an object by fd */
#define FSCONFIG_CMD_CREATE 6  /* Invoke superblock creation */
#define FSCONFIG_CMD_RECONFIGURE 7  /* Invoke superblock reconfiguration */
#endif

// fsmount flags
#ifndef FSMOUNT_CLOEXEC
#define FSMOUNT_CLOEXEC 0x00000001
#endif

PyObject *do_fsopen(const char *fs_name, unsigned int flags)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = syscall(__NR_fsopen, fs_name, flags);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR && !(async_err = PyErr_CheckSignals()));

	if (ret == -1) {
		if (!async_err) {
			PyErr_SetFromErrno(PyExc_OSError);
		}
		return NULL;
	}

	return PyLong_FromLong(ret);
}

PyObject *do_fsconfig(int fs_fd, unsigned int cmd, const char *key,
                       const void *value, int aux)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = syscall(__NR_fsconfig, fs_fd, cmd, key, value, aux);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR && !(async_err = PyErr_CheckSignals()));

	if (ret == -1) {
		if (!async_err) {
			PyErr_SetFromErrno(PyExc_OSError);
		}
		return NULL;
	}

	Py_RETURN_NONE;
}

PyObject *do_fsmount(int fs_fd, unsigned int flags, unsigned int attr_flags)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = syscall(__NR_fsmount, fs_fd, flags, attr_flags);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR && !(async_err = PyErr_CheckSignals()));

	if (ret == -1) {
		if (!async_err) {
			PyErr_SetFromErrno(PyExc_OSError);
		}
		return NULL;
	}

	return PyLong_FromLong(ret);
}

int init_fsmount_constants(PyObject *module)
{
	// fsopen flags
	if (PyModule_AddIntConstant(module, "FSOPEN_CLOEXEC", FSOPEN_CLOEXEC) < 0)
		return -1;

	// fsconfig commands
	if (PyModule_AddIntConstant(module, "FSCONFIG_SET_FLAG", FSCONFIG_SET_FLAG) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "FSCONFIG_SET_STRING", FSCONFIG_SET_STRING) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "FSCONFIG_SET_BINARY", FSCONFIG_SET_BINARY) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "FSCONFIG_SET_PATH", FSCONFIG_SET_PATH) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "FSCONFIG_SET_PATH_EMPTY", FSCONFIG_SET_PATH_EMPTY) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "FSCONFIG_SET_FD", FSCONFIG_SET_FD) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "FSCONFIG_CMD_CREATE", FSCONFIG_CMD_CREATE) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "FSCONFIG_CMD_RECONFIGURE", FSCONFIG_CMD_RECONFIGURE) < 0)
		return -1;

	// fsmount flags
	if (PyModule_AddIntConstant(module, "FSMOUNT_CLOEXEC", FSMOUNT_CLOEXEC) < 0)
		return -1;

	return 0;
}
