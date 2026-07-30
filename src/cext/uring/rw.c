// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * uring_op_rw -- the file read and write worker.
 *
 * Takes an already-validated file slot, the buffer object, and the offset from
 * the prep_pread / prep_pwrite stubs in uring.c. It pins the buffer (via the
 * buffer protocol) into a pooled op slot for the whole in-flight window, fills a
 * fixed-file READ/WRITE SQE, and hands back an opaque UringOp handle. The op
 * owns the Py_buffer from here until the completion reaps (or the handle is
 * dropped unsubmitted). The op-slot state machine is in op.h.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"

PyObject *
uring_op_rw(UringObject *self, uint32_t file_slot, PyObject *buf,
	    unsigned long long offset, bool is_write,
	    PyObject *callback, PyObject *private_data)
{
	PyObject *handle = NULL;
	uring_op_t *op = NULL;
	Py_buffer view;
	uint32_t slot = URING_NO_SLOT;

	/* Refuse a read/write on a slot whose close is already pending (before the
	 * pin, so a rejection has no buffer to release). */
	if (uring_file_check_open(self, file_slot) < 0) {
		return NULL;
	}

	/*
	 * Pin the buffer before touching the pool, so a buffer error never
	 * churns a slot. A pread destination must be writable; a pwrite source
	 * need only be readable.
	 */
	if (PyObject_GetBuffer(buf, &view,
			       is_write ? PyBUF_SIMPLE : PyBUF_WRITABLE) < 0) {
		return NULL;
	}

	/*
	 * io_uring's read/write length is a 32-bit field. A Py_ssize_t buffer
	 * larger than UINT_MAX would be silently truncated to its low 32 bits (a
	 * 4 GiB buffer to 0), transferring the wrong byte count with no error --
	 * reject it. A single op cannot move more than 4 GiB anyway.
	 */
	if (view.len > (Py_ssize_t)UINT_MAX) {
		PyBuffer_Release(&view);
		PyErr_Format(PyExc_ValueError,
			     "buffer too large for a single operation: %zd bytes "
			     "(max %u)", view.len, UINT_MAX);
		return NULL;
	}

	slot = pool_alloc(self);
	if (slot == URING_NO_SLOT) {
		PyBuffer_Release(&view);
		PyErr_SetString(PyExc_BlockingIOError,
				"op-slot pool is full: reap or drop prepared "
				"operations before preparing more");
		return NULL;
	}
	op = &self->pool[slot];
	op->tag = is_write ? URING_TAG_WRITE : URING_TAG_READ;
	op->file_slot = file_slot;
	op->u.rw.view = view;
	op->u.rw.buf_obj = Py_NewRef(buf);

	if (is_write) {
		io_uring_prep_write(&op->sqe, (int)file_slot, op->u.rw.view.buf,
				    (unsigned int)op->u.rw.view.len, offset);
	} else {
		io_uring_prep_read(&op->sqe, (int)file_slot, op->u.rw.view.buf,
				   (unsigned int)op->u.rw.view.len, offset);
	}
	io_uring_sqe_set_flags(&op->sqe, IOSQE_FIXED_FILE);

	uring_file_charge(self, op);

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_release(self, slot);
		return NULL;
	}
	return handle;
}
