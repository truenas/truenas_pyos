// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * uring_op_rw -- the vectored file read and write worker.
 *
 * Takes the already-parsed target fd, the sequence of buffer objects, the
 * offset and the RWF_* flags from the prep_preadv2 / prep_pwritev2 stubs in
 * uring.c. It pins every buffer (via the buffer protocol) into a pooled op
 * slot for the whole in-flight window, points the slot's inline iovec array at
 * the pinned views, fills a READV/WRITEV SQE, and hands back an opaque UringOp
 * handle. The op owns the Py_buffers from here until the completion reaps (or
 * the handle is dropped unsubmitted); the kernel copies the iovec array itself
 * at submission (IORING_FEAT_SUBMIT_STABLE), but reads and writes the buffers
 * it points at until the CQE. The op-slot state machine is in op.h.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"

PyObject *
uring_op_rw(UringObject *self, int fd, PyObject *buffers,
	    unsigned long long offset, int flags, bool is_write,
	    PyObject *callback, PyObject *private_data)
{
	PyObject *handle = NULL;
	PyObject *fast = NULL;
	uring_op_t *op = NULL;
	Py_ssize_t nr = 0;
	Py_ssize_t i = 0;
	uint32_t slot = URING_NO_SLOT;

	fast = PySequence_Fast(buffers,
			       "buffers must be a sequence of buffer objects");
	if (fast == NULL) {
		return NULL;
	}
	nr = PySequence_Fast_GET_SIZE(fast);

	/*
	 * The iovec and Py_buffer arrays are inline in the op slot, so the
	 * slot's capacity is the per-op ceiling. (The kernel's own UIO_MAXIOV
	 * is 1024; a larger transfer splits into multiple operations.)
	 */
	if (nr > URING_RW_IOV_CAP) {
		Py_DECREF(fast);
		PyErr_Format(PyExc_ValueError,
			     "too many buffers for a single operation: %zd "
			     "(max %u)", nr, URING_RW_IOV_CAP);
		return NULL;
	}

	slot = pool_alloc(self);
	if (slot == URING_NO_SLOT) {
		Py_DECREF(fast);
		PyErr_SetString(PyExc_BlockingIOError,
				"op-slot pool is full: reap or drop prepared "
				"operations before preparing more");
		return NULL;
	}
	op = &self->pool[slot];
	op->tag = is_write ? URING_TAG_WRITE : URING_TAG_READ;
	/*
	 * The union arm may hold another op type's stale bytes; zero the pin
	 * count before anything can fail, so every unwind from here releases
	 * exactly the views pinned so far and nothing else.
	 */
	op->u.rw.nr = 0;

	/*
	 * Pin each buffer and point its iovec at the view. A pread destination
	 * must be writable; a pwrite source need only be readable. nr counts
	 * up only after a successful pin, so the pool_recycle below (via
	 * op_free_payloads) unwinds a mid-loop failure exactly.
	 */
	for (i = 0; i < nr; i++) {
		PyObject *item = PySequence_Fast_GET_ITEM(fast, i);	/* borrowed */

		if (PyObject_GetBuffer(item, &op->u.rw.views[i],
				       is_write ? PyBUF_SIMPLE
						: PyBUF_WRITABLE) < 0) {
			Py_DECREF(fast);
			pool_recycle(self, slot);
			return NULL;
		}
		op->u.rw.iov[i].iov_base = op->u.rw.views[i].buf;
		op->u.rw.iov[i].iov_len = (size_t)op->u.rw.views[i].len;
		op->u.rw.nr++;
	}
	Py_DECREF(fast);

	if (is_write) {
		io_uring_prep_writev2(&op->sqe, fd, op->u.rw.iov,
				      (unsigned int)nr, offset, flags);
	} else {
		io_uring_prep_readv2(&op->sqe, fd, op->u.rw.iov,
				     (unsigned int)nr, offset, flags);
	}

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_recycle(self, slot);
		return NULL;
	}
	return handle;
}
