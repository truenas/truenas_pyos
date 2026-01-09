// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "umount2.h"
#include <sys/mount.h>
#include <unistd.h>

PyObject *do_umount2(const char *target, int flags)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = umount2(target, flags);
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

int init_umount2_constants(PyObject *module)
{
	// Add MNT_* and UMOUNT_* constants
#ifdef MNT_FORCE
	if (PyModule_AddIntConstant(module, "MNT_FORCE", MNT_FORCE) < 0)
		return -1;
#endif

#ifdef MNT_DETACH
	if (PyModule_AddIntConstant(module, "MNT_DETACH", MNT_DETACH) < 0)
		return -1;
#endif

#ifdef MNT_EXPIRE
	if (PyModule_AddIntConstant(module, "MNT_EXPIRE", MNT_EXPIRE) < 0)
		return -1;
#endif

#ifdef UMOUNT_NOFOLLOW
	if (PyModule_AddIntConstant(module, "UMOUNT_NOFOLLOW", UMOUNT_NOFOLLOW) < 0)
		return -1;
#endif

	return 0;
}
