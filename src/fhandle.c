// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "fhandle.h"
#include "statx.h"
#include <sys/syscall.h>
#include <unistd.h>

#define SUPPORTED_FLAGS (AT_SYMLINK_FOLLOW | AT_HANDLE_FID | \
	AT_EMPTY_PATH | AT_HANDLE_CONNECTABLE | AT_HANDLE_MNT_ID_UNIQUE)


static PyObject *py_fhandle_new(PyTypeObject *obj,
				PyObject *args_unused,
				PyObject *kwargs_unused)
{
	py_fhandle_t *self = NULL;

	self = (py_fhandle_t *)obj->tp_alloc(obj, 0);
	if (self == NULL) {
		return NULL;
	}

	self->mount_id = UINT64_MAX;  // sentinel: uninitialized
	self->fhandle = (struct file_handle *)self->fhbuf;
	self->fhandle->handle_bytes = MAX_HANDLE_SZ;
	return (PyObject *)self;
}

static int do_name_to_handle_at(py_fhandle_t *self,
				const char *path,
				int dir_fd,
				int flags)
{
	int error;
	// AT_HANDLE_MNT_ID_UNIQUE signals to the kernel that this buffer is at
	// least 64 bits wide; the kernel then writes the full 64-bit unique mount
	// ID rather than the legacy 32-bit one.  Zero-initialise so the high half
	// is clean when the flag is absent (kernel writes only 32 bits then).
	uint64_t mnt_id_buf = 0;
	int async_err = 0;

	do {
		Py_BEGIN_ALLOW_THREADS
		error = syscall(SYS_name_to_handle_at, dir_fd, path, self->fhandle,
				(int *)&mnt_id_buf, flags);
		Py_END_ALLOW_THREADS
	} while (error && errno == EINTR && !(async_err = PyErr_CheckSignals()));

	if (error) {
		if (async_err) {
			return -1;
		}
		switch (errno) {
		case ENOTDIR:
			PyErr_SetString(
				PyExc_NotADirectoryError,
				"Specified dir_fd does not refer to a "
			        "directory."
			);
			break;
		case EOPNOTSUPP:
			PyErr_SetString(
				PyExc_NotImplementedError,
				"The underlying filesystem does not support "
				"decoding of a path to file handle."
			);
			break;
		default:
			PyErr_SetFromErrno(PyExc_OSError);
		}
		return -1;
	}

	self->mount_id = mnt_id_buf;
	self->unique_mount_id = (flags & AT_HANDLE_MNT_ID_UNIQUE) != 0;
	return 0;
}

static int do_fhandle_from_bytes(py_fhandle_t *self,
				  Py_buffer *handle_buffer,
				  uint64_t mount_id,
				  bool unique_mount_id)
{
	size_t header_size;
	Py_ssize_t min_size;
	struct file_handle *src_handle = NULL;
	size_t data_bytes;

	header_size = offsetof(struct file_handle, f_handle);
	min_size = (Py_ssize_t)header_size;

	// Buffer must at least contain the header
	if (handle_buffer->len < min_size) {
		PyErr_Format(
			PyExc_ValueError,
			"handle_bytes too small: %zd (min: %zd)",
			handle_buffer->len, min_size
		);
		return -1;
	}

	if (handle_buffer->len > MAX_HANDLE_SZ) {
		PyErr_Format(
			PyExc_ValueError,
			"handle_bytes too large: %zd (max: %zd)",
			handle_buffer->len, MAX_HANDLE_SZ
		);
		return -1;
	}

	// Parse the header from the buffer
	src_handle = (struct file_handle *)handle_buffer->buf;
	data_bytes = src_handle->handle_bytes;

	if (data_bytes > handle_buffer->len - header_size) {
		PyErr_Format(
			PyExc_ValueError,
			"Incorrect encoded handle length: %zd (expected :%zd)",
			data_bytes, handle_buffer->len - header_size
		);
		return -1;
	}

	// Copy the entire structure
	memcpy(self->fhandle, handle_buffer->buf, handle_buffer->len);
	self->mount_id = mount_id;
	self->unique_mount_id = unique_mount_id;

	return 0;
}

static int py_fhandle_init(PyObject *obj,
			   PyObject *args,
			   PyObject *kwargs)
{
	int dir_fd = AT_FDCWD;
	int flags = 0;
	unsigned long long mount_id_arg = UINT64_MAX;  // sentinel: not provided
	int unique_mount_id_arg = 0;
	int rv;
	py_fhandle_t *fhandle = (py_fhandle_t *)obj;
	const char *cpath = NULL;
	Py_buffer handle_buffer = {NULL, NULL};

	const char *kwnames [] = {
		"path", "dir_fd", "flags",
		"handle_bytes", "mount_id", "unique_mount_id",
		NULL
	};

	if (!PyArg_ParseTupleAndKeywords(args, kwargs,
					 "|sii$y*Ki",
					 discard_const_p(char *, kwnames),
					 &cpath, &dir_fd, &flags,
					 &handle_buffer, &mount_id_arg,
					 &unique_mount_id_arg)) {
		return -1;
	}

	// Check if we're initializing from bytes or from path
	if (handle_buffer.buf != NULL) {
		// Initialize from bytes
		if (cpath != NULL) {
			PyBuffer_Release(&handle_buffer);
			PyErr_SetString(
				PyExc_ValueError,
				"Cannot specify both 'path' and 'handle_bytes'"
			);
			return -1;
		}

		if (mount_id_arg == UINT64_MAX) {
			PyBuffer_Release(&handle_buffer);
			PyErr_SetString(
				PyExc_ValueError,
				"'mount_id' is required when creating from 'handle_bytes'"
			);
			return -1;
		}

		rv = do_fhandle_from_bytes(fhandle, &handle_buffer,
					   (uint64_t)mount_id_arg,
					   (bool)unique_mount_id_arg);
		PyBuffer_Release(&handle_buffer);
		return rv;
	}

	// Initialize from path (existing logic)
	if (cpath == NULL) {
		PyErr_SetString(
			PyExc_ValueError,
			"Either 'path' or 'handle_bytes' must be specified"
		);
		return -1;
	}

	if ((*cpath == '/') && (dir_fd != AT_FDCWD)) {
		PyErr_SetString(
			PyExc_ValueError,
			"dir_fd may not be combined with absolute path"
		);
		return -1;
	}

	if ((*cpath == '\0') && (!(flags & AT_EMPTY_PATH) || (dir_fd == AT_FDCWD))) {
		PyErr_SetString(
			PyExc_ValueError,
			"Retrieving struct file_handle from open file descriptor "
			"requires the AT_EMPTY_FLAG in `flags` and `dir_fd` to be "
			"set to a valid file descriptor."
		);
		return -1;
	}

	if (flags & ~SUPPORTED_FLAGS) {
		PyErr_SetString(
			PyExc_ValueError,
			"Unsupported flags combination. Supported flags are: "
			"AT_SYMLINK_FOLLOW, AT_HANDLE_FID, AT_EMPTY_PATH."
		);
		return -1;
	}

	return do_name_to_handle_at(fhandle, cpath, dir_fd, flags);
}

PyDoc_STRVAR(py_fhandle_open__doc__,
"open()\n"
"--\n\n"
"Open a regular file descriptor from the underlying file handle\n"
"Parameters\n"
"----------\n"
"None\n\n"
"Returns\n"
"-------\n"
"None\n"
);

static PyObject *py_fhandle_open(PyObject *obj,
				 PyObject *args,
				 PyObject *kwargs)
{
	int mount_fd;
	int flags = 0;
	int error;
	int fd;
	struct statx st;
	const char *kwnames [] = { "mount_fd", "flags", NULL };
	py_fhandle_t *self = (py_fhandle_t *)obj;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs,
					 "i|i",
					 discard_const_p(char *, kwnames),
					 &mount_fd, &flags)) {
		return NULL;
	}

	if (self->mount_id == UINT64_MAX) {
		PyErr_SetString(
			PyExc_ValueError,
			"Invalid File Handle"
		);
		return NULL;
	}

	// Use STATX_MNT_ID_UNIQUE when the handle carries a unique mount ID
	// (obtained via AT_HANDLE_MNT_ID_UNIQUE), otherwise use the legacy
	// STATX_MNT_ID.  Both store the mount ID in stx_mnt_id; we compare
	// the low 32 bits (int cast) to self->mount_id in both cases.
	unsigned int statx_mask = self->unique_mount_id
		? STATX_MNT_ID_UNIQUE
		: STATX_MNT_ID;

	int async_err = 0;
	do {
		Py_BEGIN_ALLOW_THREADS
		error = statx(mount_fd, "", AT_EMPTY_PATH, statx_mask, &st);
		Py_END_ALLOW_THREADS
	} while (error && errno == EINTR && !(async_err = PyErr_CheckSignals()));

	if (error) {
		if (!async_err) {
			PyErr_SetFromErrno(PyExc_OSError);
		}
		return NULL;
	}

	if (st.stx_mnt_id != self->mount_id) {
		PyErr_SetString(
			PyExc_ValueError,
			"Filesystem underlying `mount_fd` parameter does "
			"not match the filesystem under which the handle "
			"was opened."
		);
		return NULL;
	}

	async_err = 0;
	do {
		Py_BEGIN_ALLOW_THREADS
		fd = syscall(SYS_open_by_handle_at, mount_fd, self->fhandle, flags);
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

PyDoc_STRVAR(py_fhandle_bytes__doc__,
"__bytes__()\n"
"--\n\n"
"Return the serialized file handle structure.\n"
"Includes handle_bytes, handle_type, and the handle data.\n"
"Parameters\n"
"----------\n"
"None\n\n"
"Returns\n"
"-------\n"
"bytes\n"
"    The complete serialized file handle structure\n"
);

static PyObject *py_fhandle_bytes(PyObject *obj,
				  PyObject *args_unused)
{
	py_fhandle_t *self = (py_fhandle_t *)obj;
	size_t total_size;

	if (self->mount_id == UINT64_MAX) {
		PyErr_SetString(
			PyExc_ValueError,
			"Cannot get bytes from uninitialized file handle"
		);
		return NULL;
	}

	if (self->fhandle == NULL) {
		PyErr_SetString(
			PyExc_ValueError,
			"File handle is NULL"
		);
		return NULL;
	}

	// Return the entire struct file_handle including header and data
	total_size = offsetof(struct file_handle, f_handle) + self->fhandle->handle_bytes;
	return PyBytes_FromStringAndSize(
		(const char *)self->fhandle,
		total_size
	);
}

static PyMethodDef py_fhandle_methods[] = {
	{
		.ml_name = "open",
		.ml_meth = (PyCFunction)py_fhandle_open,
		.ml_flags = METH_VARARGS|METH_KEYWORDS,
		.ml_doc = py_fhandle_open__doc__
	},
	{
		.ml_name = "__bytes__",
		.ml_meth = (PyCFunction)py_fhandle_bytes,
		.ml_flags = METH_NOARGS,
		.ml_doc = py_fhandle_bytes__doc__
	},
	{ NULL, NULL, 0, NULL }
};

static PyObject *py_fhandle_mount_id(PyObject *obj, void *closure)
{
	py_fhandle_t *self = (py_fhandle_t *)obj;
	if (self->mount_id == UINT64_MAX) {
		Py_RETURN_NONE;
	}

	return PyLong_FromUnsignedLongLong(self->mount_id);
}

static PyGetSetDef py_fhandle_getsetters[] = {
	{
		.name   = discard_const_p(char, "mount_id"),
		.get    = (getter)py_fhandle_mount_id,
	},
	{ .name = NULL }
};

static PyObject *py_fhandle_repr(PyObject *obj)
{
        py_fhandle_t *self = (py_fhandle_t *)obj;

        if (self->mount_id == UINT64_MAX) {
                return PyUnicode_FromString(
                        "truenas_os.Fhandle(<UNINITIALIZED>)"
                );
        }

        return PyUnicode_FromFormat(
                "truenas_os.Fhandle(mount_id=%llu, may_open=%s)",
                (unsigned long long)self->mount_id,
                self->is_handle_fd ? "False" : "True"
        );
}

PyDoc_STRVAR(py_fhandle__doc__,
"Python wrapper for struct file_handle.\n"
);

PyTypeObject PyFhandle = {
	.tp_name = "truenas_os.Fhandle",
	.tp_basicsize = sizeof(py_fhandle_t),
	.tp_methods = py_fhandle_methods,
	.tp_getset = py_fhandle_getsetters,
	.tp_new = py_fhandle_new,
	.tp_init = py_fhandle_init,
	.tp_repr = py_fhandle_repr,
	.tp_doc = py_fhandle__doc__,
	.tp_flags = Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE,
};
