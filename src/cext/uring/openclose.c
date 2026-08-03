// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * uring_op_openat2 / uring_op_close -- the file open and close workers.
 * Already-parsed C arguments come from the prep stubs in uring.c; the op-slot
 * state machine is in op.h.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"

PyObject *
uring_op_openat2(UringObject *self, int dirfd, PyObject *path_bytes,
		 int flags, int mode, unsigned long long resolve,
		 PyObject *callback, PyObject *private_data)
{
	PyObject *handle = NULL;
	uring_op_t *op = NULL;
	uint32_t slot = URING_NO_SLOT;

	slot = pool_alloc(self);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError,
				"op-slot pool is full: reap or drop prepared "
				"operations before preparing more");
		return NULL;
	}
	op = &self->pool[slot];
	op->tag = URING_TAG_OPEN;

	/*
	 * The SQE points into the path bytes rather than an inline copy; the
	 * owned ref keeps the immutable buffer alive until the op is released
	 * (the kernel getname()s its own copy at submission). An over-long
	 * path is the kernel's ENAMETOOLONG at completion, not policed here.
	 */
	op->path_bytes = Py_NewRef(path_bytes);

	op->u.open.how.flags = (__u64)(unsigned int)flags;
	op->u.open.how.mode = (__u64)(unsigned int)mode;
	op->u.open.how.resolve = (__u64)resolve;

	io_uring_prep_openat2(&op->sqe, dirfd, PyBytes_AS_STRING(op->path_bytes),
			      &op->u.open.how);

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_recycle(self, slot);
		return NULL;
	}
	return handle;
}

PyObject *
uring_op_close(UringObject *self, int fd,
	       PyObject *callback, PyObject *private_data)
{
	PyObject *handle = NULL;
	uring_op_t *op = NULL;
	uint32_t slot = URING_NO_SLOT;

	slot = pool_alloc(self);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError,
				"op-slot pool is full: reap or drop prepared "
				"operations before preparing more");
		return NULL;
	}
	op = &self->pool[slot];
	op->tag = URING_TAG_CLOSE;

	io_uring_prep_close(&op->sqe, fd);

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_recycle(self, slot);
		return NULL;
	}
	return handle;
}
