// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "renameat2.h"
#include <unistd.h>
#include <errno.h>

#ifndef AT_RENAME_NOREPLACE
#define AT_RENAME_NOREPLACE 0x0001
#endif

#ifndef AT_RENAME_EXCHANGE
#define AT_RENAME_EXCHANGE 0x0002
#endif

#ifndef AT_RENAME_WHITEOUT
#define AT_RENAME_WHITEOUT 0x0004
#endif

PyObject *do_renameat2(int olddirfd, const char *oldpath,
                       int newdirfd, const char *newpath,
                       unsigned int flags)
{
	int ret;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = renameat2(olddirfd, oldpath, newdirfd, newpath, flags);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR && !(async_err = PyErr_CheckSignals()));

	if (ret == -1) {
		if (!async_err) {
			PyErr_SetFromErrnoWithFilename(PyExc_OSError, oldpath);
		}
		return NULL;
	}

	Py_RETURN_NONE;
}

int init_renameat2_constants(PyObject *module)
{
	// Add AT_RENAME_* constants
	if (PyModule_AddIntConstant(module, "AT_RENAME_NOREPLACE", AT_RENAME_NOREPLACE) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "AT_RENAME_EXCHANGE", AT_RENAME_EXCHANGE) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "AT_RENAME_WHITEOUT", AT_RENAME_WHITEOUT) < 0)
		return -1;

	return 0;
}
