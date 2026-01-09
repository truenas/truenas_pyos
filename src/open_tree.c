// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "open_tree.h"
#include <sys/syscall.h>
#include <unistd.h>

#define __NR_open_tree 428

PyObject *do_open_tree(int dirfd, const char *pathname,
                       unsigned int flags)
{
	int fd;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		fd = syscall(__NR_open_tree, dirfd, pathname, flags);
		Py_END_ALLOW_THREADS
	} while (fd == -1 && errno == EINTR && !(async_err = PyErr_CheckSignals()));

	if (fd == -1) {
		if (!async_err) {
			PyErr_SetFromErrno(PyExc_OSError);
		}
		return NULL;
	}

	return PyLong_FromLong(fd);
}

int init_open_tree_constants(PyObject *module)
{
	// Add OPEN_TREE_* constants
	if (PyModule_AddIntConstant(module, "OPEN_TREE_CLONE", OPEN_TREE_CLONE) < 0)
		return -1;

#ifdef OPEN_TREE_CLOEXEC
	if (PyModule_AddIntConstant(module, "OPEN_TREE_CLOEXEC", OPEN_TREE_CLOEXEC) < 0)
		return -1;
#endif

	return 0;
}
