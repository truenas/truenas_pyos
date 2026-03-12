// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "mount_setattr.h"
#include <sys/syscall.h>
#include <unistd.h>

#define __NR_mount_setattr 442
#define AT_RECURSIVE 0x8000  /* Apply to the entire subtree */

PyObject *do_mount_setattr(int dirfd, const char *pathname,
                            unsigned int flags,
                            struct mount_attr *attr)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = syscall(__NR_mount_setattr, dirfd, pathname, flags,
		              attr, MOUNT_ATTR_SIZE_VER0);
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

int init_mount_setattr_constants(PyObject *module)
{
	// AT_RECURSIVE flag for mount_setattr
	if (PyModule_AddIntConstant(module, "AT_RECURSIVE", AT_RECURSIVE) < 0)
		return -1;

	return 0;
}
