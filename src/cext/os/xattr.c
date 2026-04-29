// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include <errno.h>
#include <string.h>
#include <sys/xattr.h>
#include "xattr.h"


/*
 * do_fgetxattr - probe size, allocate a PyBytes of that size, read into
 * its underlying buffer, and trim to the actual length with
 * _PyBytes_Resize.  This avoids a separate heap-buffer + memcpy step.
 *
 * The outer loop re-probes on ERANGE during the read: the value may
 * have grown between our probe and our read (concurrent setxattr).
 * EINTR is retried with PyErr_CheckSignals(); the GIL is released
 * around each syscall.
 */
PyObject *
do_fgetxattr(int fd, const char *name)
{
	PyObject *result = NULL;
	ssize_t probe_size = 0;
	ssize_t read_size = 0;
	size_t bufsize = 0;
	char *buf = NULL;
	int async_err = 0;

	for (;;) {
		do {
			Py_BEGIN_ALLOW_THREADS
			probe_size = fgetxattr(fd, name, NULL, 0);
			Py_END_ALLOW_THREADS
		} while (probe_size == -1 && errno == EINTR &&
		         !(async_err = PyErr_CheckSignals()));

		if (async_err) {
			Py_XDECREF(result);
			return NULL;
		}
		if (probe_size == -1) {
			PyErr_SetFromErrno(PyExc_OSError);
			Py_XDECREF(result);
			return NULL;
		}
		if ((size_t)probe_size > TRUENAS_XATTR_SIZE_MAX) {
			errno = E2BIG;
			PyErr_SetFromErrno(PyExc_OSError);
			Py_XDECREF(result);
			return NULL;
		}

		/*
		 * Allocate (or reallocate) a PyBytes object of the probed
		 * size, with a 1-byte minimum so the buffer is always a
		 * valid pointer for the syscall.  PyBytes_FromStringAndSize
		 * with NULL data initialises an uninitialised mutable bytes
		 * we can write into via PyBytes_AS_STRING.
		 */
		bufsize = probe_size > 0 ? (size_t)probe_size : 1;
		result = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)bufsize);
		if (result == NULL)
			return NULL;
		buf = PyBytes_AS_STRING(result);

		do {
			Py_BEGIN_ALLOW_THREADS
			read_size = fgetxattr(fd, name, buf, bufsize);
			Py_END_ALLOW_THREADS
		} while (read_size == -1 && errno == EINTR &&
		         !(async_err = PyErr_CheckSignals()));

		if (async_err) {
			Py_DECREF(result);
			return NULL;
		}

		if (read_size >= 0)
			break;

		if (errno == ERANGE) {
			/*
			 * xattr mutated / grew while we were getting it
			 * so loop again to redo buffer allocation
			 */
			Py_CLEAR(result);
			continue;
		}

		PyErr_SetFromErrno(PyExc_OSError);
		Py_DECREF(result);
		return NULL;
	}

	/*
	 * read_size <= bufsize is guaranteed by the syscall, so this can
	 * only shrink the bytes object.  When the empty case made us
	 * round bufsize up to 1 we still want to land on a zero-length
	 * result, so an unconditional resize on length mismatch is fine.
	 */
	if ((size_t)read_size != bufsize) {
		if (_PyBytes_Resize(&result, read_size) < 0)
			return NULL;
	}
	return result;
}


/*
 * do_flistxattr - try a small fast-path buffer first and escalate to
 * the kernel-defined XATTR_LIST_MAX on ERANGE.  Mirrors CPython's
 * `os_listxattr_impl` pattern (Modules/posixmodule.c) — the name list
 * is typically tens of bytes, so a single 256-byte alloc covers almost
 * every call without any probe syscall.  When that ERANGEs we go
 * straight to XATTR_LIST_MAX since there is no useful intermediate
 * size.  Note that XATTR_LIST_MAX is the kernel's cap on the *name
 * list*, distinct from the per-value cap TRUENAS_XATTR_SIZE_MAX used
 * by fgetxattr / fsetxattr.
 */
PyObject *
do_flistxattr(int fd)
{
	static const size_t buffer_sizes[] = {
	    256, XATTR_LIST_MAX, 0
	};
	char *buf = NULL;
	size_t bufsize = 0;
	ssize_t read_size = -1;
	int async_err = 0;
	int i = 0;
	PyObject *result = NULL;
	PyObject *name_obj = NULL;
	const char *p = NULL;
	const char *end = NULL;
	size_t name_len = 0;
	int rc = 0;

	for (i = 0; (bufsize = buffer_sizes[i]) != 0; i++) {
		buf = PyMem_RawMalloc(bufsize);
		if (buf == NULL) {
			PyErr_NoMemory();
			return NULL;
		}

		do {
			Py_BEGIN_ALLOW_THREADS
			read_size = flistxattr(fd, buf, bufsize);
			Py_END_ALLOW_THREADS
		} while (read_size == -1 && errno == EINTR &&
		         !(async_err = PyErr_CheckSignals()));

		if (async_err)
			goto cleanup;

		if (read_size >= 0)
			break;

		if (errno == ERANGE) {
			/* try the next (larger) buffer. */
			PyMem_RawFree(buf);
			buf = NULL;
			continue;
		}

		PyErr_SetFromErrno(PyExc_OSError);
		goto cleanup;
	}

	if (read_size == -1) {
		/*
		 * Buffer-size array exhausted — only reachable after ERANGE
		 * at XATTR_LIST_MAX, i.e. the kernel-side name list does
		 * not fit in the kernel's own list cap.
		 */
		errno = E2BIG;
		PyErr_SetFromErrno(PyExc_OSError);
		goto cleanup;
	}

	result = PyList_New(0);
	if (result == NULL)
		goto cleanup;

	p = buf;
	end = buf + read_size;
	while (p < end) {
		name_len = strlen(p);
		name_obj = PyUnicode_DecodeFSDefaultAndSize(
		    p, (Py_ssize_t)name_len);
		if (name_obj == NULL) {
			Py_CLEAR(result);
			goto cleanup;
		}
		rc = PyList_Append(result, name_obj);
		Py_DECREF(name_obj);
		if (rc < 0) {
			Py_CLEAR(result);
			goto cleanup;
		}
		p += name_len + 1;
	}

cleanup:
	PyMem_RawFree(buf);
	return result;
}


int
do_fsetxattr(int fd, const char *name,
             const char *value, size_t value_len, int flags)
{
	int ret = 0;
	int async_err = 0;

	/*
	 * Reject anything other than the documented modes.  The kernel
	 * also returns EINVAL in this case but raising ValueError up-front
	 * gives a more diagnostic message.
	 */
	if (flags != 0 && flags != XATTR_CREATE && flags != XATTR_REPLACE) {
		PyErr_Format(PyExc_ValueError,
		             "fsetxattr: flags must be 0, XATTR_CREATE, "
		             "or XATTR_REPLACE (got %d)", flags);
		return -1;
	}

	/*
	 * Short-circuit oversized values before issuing the syscall so the
	 * caller gets a clear error from us rather than a delayed E2BIG
	 * from the kernel after a wasted argument-buffer copy.
	 */
	if (value_len > TRUENAS_XATTR_SIZE_MAX) {
		errno = E2BIG;
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = fsetxattr(fd, name, value, value_len, flags);
		Py_END_ALLOW_THREADS
	} while (ret == -1 && errno == EINTR &&
	         !(async_err = PyErr_CheckSignals()));

	if (async_err)
		return -1;

	if (ret == -1) {
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	return 0;
}


int
init_xattr_constants(PyObject *module)
{
	if (PyModule_AddIntConstant(module, "XATTR_CREATE", XATTR_CREATE) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "XATTR_REPLACE", XATTR_REPLACE) < 0)
		return -1;
	if (PyModule_AddIntConstant(module, "XATTR_SIZE_MAX",
	                            TRUENAS_XATTR_SIZE_MAX) < 0)
		return -1;
	return 0;
}
