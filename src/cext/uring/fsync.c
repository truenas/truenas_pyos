// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * uring_op_fsync -- the file-sync worker.
 *
 * Takes already-parsed C arguments from the prep_fsync stub in uring.c and
 * builds one fsync operation: pop an op slot, fill the SQE, and hand back an
 * opaque UringOp handle. The kernel runs it as fsync(2) -- or fdatasync(2)
 * with IORING_FSYNC_DATASYNC -- over the whole file or a byte range. The
 * op-slot state machine is in op.h.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"

PyObject *
uring_op_fsync(UringObject *self, int fd, int datasync,
	       unsigned long long offset, unsigned int length,
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
	op->tag = URING_TAG_FSYNC;

	io_uring_prep_fsync(&op->sqe, fd,
			    datasync ? IORING_FSYNC_DATASYNC : 0);
	/*
	 * The liburing helper zeroes off/len (= sync the whole file); a byte
	 * range is set directly on the SQE afterward, per io_uring_prep_fsync(3).
	 * The kernel syncs [off, off + len] via vfs_fsync_range, treating
	 * off == len == 0 as "through end of file".
	 */
	op->sqe.off = offset;
	op->sqe.len = length;

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_recycle(self, slot);
		return NULL;
	}
	return handle;
}
