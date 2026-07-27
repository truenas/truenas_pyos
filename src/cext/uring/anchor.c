// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * Anchor and Leaf -- the confinement primitives.
 *
 * The reactor exposes no path-based call that starts from AT_FDCWD or an
 * absolute path.  Every resolution is relative to an Anchor, and every *at
 * operation names a single validated component beneath it.
 *
 * Two independent kernel facts force this shape:
 *
 *   - Anchors must be *real* fds.  Each io_uring path op resolves its dirfd
 *     through the ordinary fd table at prep time and rejects a fixed-table
 *     index with -EBADF, so a registered file cannot serve as a dirfd.
 *
 *   - The plain *at opcodes (RENAMEAT, UNLINKAT, MKDIRAT, SYMLINKAT, LINKAT)
 *     honour no RESOLVE_* flags at all.  Only OPENAT2 takes an open_how and
 *     can be confined in-kernel with RESOLVE_BENEATH.  For everything else
 *     the single-component rule *is* the confinement -- a name containing
 *     '/' or ".." would walk wherever it liked.
 *
 * A personality decides *who* an operation runs as; it carries no cwd, root
 * or mount namespace (struct cred holds no fs_struct).  The Anchor is what
 * decides *where*.
 */

#include <Python.h>
#include "common/includes.h"
#include "anchor.h"

/* -- Leaf validation ------------------------------------------------------ */

int
uring_leaf_convert(PyObject *obj, PyObject **owner, const char **out,
		   Py_ssize_t *len)
{
	PyObject *bytes = NULL;
	const char *buf = NULL;
	Py_ssize_t size = 0;

	*owner = NULL;
	*out = NULL;
	*len = 0;

	/*
	 * PyUnicode_FSConverter accepts str, bytes and os.PathLike, applying
	 * the filesystem encoding with surrogateescape -- the same conversion
	 * os.open() performs, and it rejects embedded NUL for us.
	 */
	if (!PyUnicode_FSConverter(obj, &bytes)) {
		return -1;
	}

	if (PyBytes_AsStringAndSize(bytes, (char **)&buf, &size) < 0) {
		Py_DECREF(bytes);
		return -1;
	}

	if (size == 0) {
		PyErr_SetString(PyExc_ValueError,
				"leaf must not be empty");
		goto fail;
	}

	if (memchr(buf, '/', (size_t)size) != NULL) {
		PyErr_Format(PyExc_ValueError,
			     "leaf must be a single path component, got %R -- "
			     "the *at opcodes honour no RESOLVE_* flags, so a "
			     "multi-component name would escape the anchor",
			     obj);
		goto fail;
	}

	if (size == 2 && buf[0] == '.' && buf[1] == '.') {
		PyErr_SetString(PyExc_ValueError,
				"leaf must not be '..'");
		goto fail;
	}

	if (size == 1 && buf[0] == '.') {
		PyErr_SetString(PyExc_ValueError,
				"leaf must not be '.'");
		goto fail;
	}

	*owner = bytes;
	*out = buf;
	*len = size;
	return 0;

fail:
	Py_DECREF(bytes);
	return -1;
}

/* -- Anchor --------------------------------------------------------------- */

PyDoc_STRVAR(anchor__doc__,
"Anchor(path)\n"
"--\n\n"
"A real directory file descriptor that path operations resolve against.\n\n"
"Opened O_PATH|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW. O_PATH is sufficient: an\n"
"anchor is only ever used as a dirfd, never read or written.\n\n"
"The fd must be a real descriptor, not a registered (fixed-table) index --\n"
"every io_uring path operation resolves its dirfd through the ordinary fd\n"
"table and rejects a fixed index with EBADF.\n\n"
"Parameters\n"
"----------\n"
"path : str or bytes or os.PathLike\n"
"    Directory to anchor at.\n\n"
"Raises\n"
"------\n"
"NotADirectoryError\n"
"    If path is not a directory.\n"
"OSError\n"
"    If the directory cannot be opened.\n");

static PyObject *
Anchor_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = {"path", NULL};
	AnchorObject *self = NULL;
	PyObject *path_obj = NULL;
	PyObject *path_bytes = NULL;
	const char *path = NULL;
	int fd = -1;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O:Anchor", kwlist,
					 &path_obj)) {
		return NULL;
	}

	if (!PyUnicode_FSConverter(path_obj, &path_bytes)) {
		return NULL;
	}
	path = PyBytes_AS_STRING(path_bytes);

	Py_BEGIN_ALLOW_THREADS
	fd = open(path, O_PATH | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
	Py_END_ALLOW_THREADS

	if (fd < 0) {
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
		Py_DECREF(path_bytes);
		return NULL;
	}

	self = (AnchorObject *)type->tp_alloc(type, 0);
	if (self == NULL) {
		close(fd);
		Py_DECREF(path_bytes);
		return NULL;
	}

	self->fd = fd;
	self->path = path_bytes;	/* steals the reference */
	return (PyObject *)self;
}

static void
Anchor_dealloc(AnchorObject *self)
{
	if (self->fd >= 0) {
		close(self->fd);
		self->fd = -1;
	}
	Py_CLEAR(self->path);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
Anchor_repr(AnchorObject *self)
{
	if (self->fd < 0) {
		return PyUnicode_FromString("<Anchor (closed)>");
	}
	return PyUnicode_FromFormat("<Anchor fd=%d path=%R>",
				    self->fd, self->path);
}

PyDoc_STRVAR(Anchor_fileno__doc__,
"fileno()\n"
"--\n\n"
"Return the underlying directory file descriptor.\n\n"
"The descriptor is owned by the Anchor; do not close it.\n");

static PyObject *
Anchor_fileno(AnchorObject *self, PyObject *Py_UNUSED(ignored))
{
	if (self->fd < 0) {
		PyErr_SetString(PyExc_ValueError, "Anchor is closed");
		return NULL;
	}
	return PyLong_FromLong(self->fd);
}

PyDoc_STRVAR(Anchor_close__doc__,
"close()\n"
"--\n\n"
"Close the anchor's descriptor. Idempotent.\n\n"
"Operations already submitted against this anchor are unaffected: io_uring\n"
"resolved the dirfd at prep time, before the operation was queued.\n");

static PyObject *
Anchor_close(AnchorObject *self, PyObject *Py_UNUSED(ignored))
{
	if (self->fd >= 0) {
		close(self->fd);
		self->fd = -1;
	}
	Py_RETURN_NONE;
}

static PyObject *
Anchor_enter(PyObject *self, PyObject *Py_UNUSED(ignored))
{
	return Py_NewRef(self);
}

static PyObject *
Anchor_exit(AnchorObject *self, PyObject *Py_UNUSED(args))
{
	return Anchor_close(self, NULL);
}

static PyObject *
Anchor_get_path(AnchorObject *self, void *Py_UNUSED(closure))
{
	if (self->path == NULL) {
		Py_RETURN_NONE;
	}
	return Py_NewRef(self->path);
}

static PyObject *
Anchor_get_closed(AnchorObject *self, void *Py_UNUSED(closure))
{
	return PyBool_FromLong(self->fd < 0);
}

static PyMethodDef Anchor_methods[] = {
	{
		.ml_name = "fileno",
		.ml_meth = (PyCFunction)Anchor_fileno,
		.ml_flags = METH_NOARGS,
		.ml_doc = Anchor_fileno__doc__
	},
	{
		.ml_name = "close",
		.ml_meth = (PyCFunction)Anchor_close,
		.ml_flags = METH_NOARGS,
		.ml_doc = Anchor_close__doc__
	},
	{
		.ml_name = "__enter__",
		.ml_meth = (PyCFunction)Anchor_enter,
		.ml_flags = METH_NOARGS,
		.ml_doc = NULL
	},
	{
		.ml_name = "__exit__",
		.ml_meth = (PyCFunction)Anchor_exit,
		.ml_flags = METH_VARARGS,
		.ml_doc = NULL
	},
	{ .ml_name = NULL },
};

static PyGetSetDef Anchor_getsetters[] = {
	{
		.name = discard_const_p(char, "path"),
		.get = (getter)Anchor_get_path,
		.doc = discard_const_p(char, "Directory path this anchor was opened on (bytes)")
	},
	{
		.name = discard_const_p(char, "closed"),
		.get = (getter)Anchor_get_closed,
		.doc = discard_const_p(char, "True once close() has been called")
	},
	{ .name = NULL },
};

PyTypeObject AnchorType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_os.uring.Anchor",
	.tp_basicsize = sizeof(AnchorObject),
	.tp_dealloc = (destructor)Anchor_dealloc,
	.tp_repr = (reprfunc)Anchor_repr,
	.tp_methods = Anchor_methods,
	.tp_getset = Anchor_getsetters,
	.tp_new = Anchor_new,
	.tp_doc = anchor__doc__,
	.tp_flags = Py_TPFLAGS_DEFAULT,
};

int
init_anchor_type(PyObject *module)
{
	if (PyType_Ready(&AnchorType) < 0) {
		return -1;
	}

	if (PyModule_AddObjectRef(module, "Anchor", (PyObject *)&AnchorType) < 0) {
		return -1;
	}

	return 0;
}
