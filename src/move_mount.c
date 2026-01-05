// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "move_mount.h"
#include <sys/syscall.h>
#include <unistd.h>

#define __NR_move_mount 429

PyObject *do_move_mount(int from_dirfd, const char *from_pathname,
                         int to_dirfd, const char *to_pathname,
                         unsigned int flags)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = syscall(__NR_move_mount, from_dirfd, from_pathname,
		              to_dirfd, to_pathname, flags);
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

int init_move_mount_constants(PyObject *module)
{
	// Add MOVE_MOUNT_* constants
	if (PyModule_AddIntConstant(module, "MOVE_MOUNT_F_SYMLINKS", MOVE_MOUNT_F_SYMLINKS) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "MOVE_MOUNT_F_AUTOMOUNTS", MOVE_MOUNT_F_AUTOMOUNTS) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "MOVE_MOUNT_F_EMPTY_PATH", MOVE_MOUNT_F_EMPTY_PATH) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "MOVE_MOUNT_T_SYMLINKS", MOVE_MOUNT_T_SYMLINKS) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "MOVE_MOUNT_T_AUTOMOUNTS", MOVE_MOUNT_T_AUTOMOUNTS) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "MOVE_MOUNT_T_EMPTY_PATH", MOVE_MOUNT_T_EMPTY_PATH) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "MOVE_MOUNT_SET_GROUP", MOVE_MOUNT_SET_GROUP) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "MOVE_MOUNT_BENEATH", MOVE_MOUNT_BENEATH) < 0)
		return -1;

	return 0;
}
