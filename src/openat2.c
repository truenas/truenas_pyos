// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "openat2.h"
#include <sys/syscall.h>
#include <unistd.h>

#define __NR_openat2 437

PyObject *do_openat2(int dirfd, const char *pathname,
                      uint64_t flags, uint64_t mode, uint64_t resolve)
{
	struct open_how how = {
		.flags = flags,
		.mode = mode,
		.resolve = resolve,
	};
	int fd;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		fd = syscall(__NR_openat2, dirfd, pathname, &how, sizeof(how));
		Py_END_ALLOW_THREADS
	} while (fd == -1 && errno == EINTR && !(async_err = PyErr_CheckSignals()));

	if (fd == -1) {
		if (!async_err) {
			PyErr_SetFromErrno(PyExc_OSError);
		}
		return NULL;
	}

	return Py_BuildValue("i", fd);
}

/*
 * C wrapper for openat2() - returns fd or -1 with errno set
 */
int openat2_impl(int dirfd, const char *pathname, int flags, uint64_t resolve_flags)
{
	struct open_how how;
	int fd;

	how.flags = flags;
	how.mode = 0;
	how.resolve = resolve_flags;

	fd = syscall(__NR_openat2, dirfd, pathname, &how, sizeof(how));
	return fd;
}

int init_openat2_constants(PyObject *module)
{
	// Add RESOLVE_* constants
	if (PyModule_AddIntConstant(module, "RESOLVE_NO_XDEV", RESOLVE_NO_XDEV) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "RESOLVE_NO_MAGICLINKS", RESOLVE_NO_MAGICLINKS) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "RESOLVE_NO_SYMLINKS", RESOLVE_NO_SYMLINKS) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "RESOLVE_BENEATH", RESOLVE_BENEATH) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "RESOLVE_IN_ROOT", RESOLVE_IN_ROOT) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "RESOLVE_CACHED", RESOLVE_CACHED) < 0)
		return -1;

	return 0;
}
