// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * uring_op_openat2 / uring_op_close / uring_op_fixed_fd_install -- the file
 * open, close, and fd-install workers.
 *
 * These take already-parsed C arguments from the prep_* stubs in uring.c and
 * build one prepared operation: pop an op slot, fill a standalone SQE, and hand
 * back an opaque UringOp handle. open installs a file directly into the ring's
 * registered (fixed) file table via OPENAT2 direct-install (no process fd is
 * created); close frees that slot; fixed_fd_install mints a regular process fd
 * from a slot (FIXED_FD_INSTALL) -- the one way a registered file leaves the
 * ring. The op-slot state machine they lean on is in op.h.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"

PyObject *
uring_op_openat2(UringObject *self, int dirfd, const char *path,
		 Py_ssize_t path_len, int flags, int mode,
		 unsigned long long resolve,
		 PyObject *callback, PyObject *private_data)
{
	PyObject *handle = NULL;
	uring_op_t *op = NULL;
	uint32_t slot = URING_NO_SLOT;
	uint32_t file_slot = URING_NO_SLOT;

	if (path_len >= PATH_MAX) {
		PyErr_SetString(PyExc_ValueError,
				"path too long (must be < PATH_MAX)");
		return NULL;
	}

	slot = pool_alloc(self);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError,
				"op-slot pool is full: reap or drop prepared "
				"operations before preparing more");
		return NULL;
	}
	op = &self->pool[slot];
	op->tag = URING_TAG_OPEN;

	file_slot = uring_file_alloc(self);
	if (file_slot == URING_NO_SLOT) {
		pool_release(self, slot);
		PyErr_SetString(PyExc_OSError, "registered file table is full");
		return NULL;
	}
	op->file_slot = file_slot;
	op->owns_slot = true;

	/* path fits (checked above); copy it inline into the slot. */
	memcpy(op->u.open.path, path, (size_t)path_len + 1);

	op->u.open.how.flags = (__u64)(unsigned int)flags;
	op->u.open.how.mode = (__u64)(unsigned int)mode;
	op->u.open.how.resolve = (__u64)resolve;

	io_uring_prep_openat2_direct(&op->sqe, dirfd, op->u.open.path,
				     &op->u.open.how, file_slot);

	uring_file_charge(self, op);	/* count the open against its fresh slot */

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_release(self, slot);
		return NULL;
	}
	return handle;
}

PyObject *
uring_op_close(UringObject *self, uint32_t file_slot,
	       PyObject *callback, PyObject *private_data)
{
	PyObject *handle = NULL;
	uring_op_t *op = NULL;
	uint32_t slot = URING_NO_SLOT;

	/*
	 * The close frees the slot, so it must be the slot's last operation: refuse
	 * it while any other op on the slot is live -- prepared or in flight. The
	 * per-file live counter (charged at prep, released at reap/drop) covers a
	 * sibling still staged in the same batch, which is still PREPPED: an unordered
	 * close batched with that read/write could otherwise unregister the slot while
	 * the sibling still references it.
	 */
	if (self->files[file_slot].live > 0) {
		PyErr_Format(PyExc_BlockingIOError,
			     "file slot still has %u operation(s) pending; the "
			     "slot-freeing close must be the file's last operation",
			     self->files[file_slot].live);
		return NULL;
	}

	slot = pool_alloc(self);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError,
				"op-slot pool is full: reap or drop prepared "
				"operations before preparing more");
		return NULL;
	}
	op = &self->pool[slot];
	op->tag = URING_TAG_CLOSE;
	op->file_slot = file_slot;

	io_uring_prep_close_direct(&op->sqe, file_slot);

	uring_file_charge(self, op);
	if (self->files[file_slot].in_use) {
		/*
		 * Block any further op from being prepared on this slot until the close
		 * reaps (the reverse of the live > 0 guard above). Only meaningful for a
		 * real registration; a close of an empty slot is a bare EBADF.
		 */
		self->files[file_slot].close_pending = true;
	}

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_release(self, slot);
		return NULL;
	}
	return handle;
}

PyObject *
uring_op_fixed_fd_install(UringObject *self, uint32_t file_slot, int cloexec,
			  PyObject *callback, PyObject *private_data)
{
	PyObject *handle = NULL;
	uring_op_t *op = NULL;
	uint32_t slot = URING_NO_SLOT;
	unsigned int flags = cloexec ? 0U : IORING_FIXED_FD_NO_CLOEXEC;

	if (uring_file_check_open(self, file_slot) < 0) {
		return NULL;
	}

	slot = pool_alloc(self);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError,
				"op-slot pool is full: reap or drop prepared "
				"operations before preparing more");
		return NULL;
	}
	op = &self->pool[slot];
	op->tag = URING_TAG_INSTALL;
	op->file_slot = file_slot;	/* counted in-flight on the slot, not owned */

	/*
	 * Duplicates, not moves: the kernel's receive_fd() takes a fresh reference
	 * to the slot's file and installs it at a new process fd (the completion
	 * result), leaving the registered slot intact -- both must be closed. The
	 * liburing helper sets IOSQE_FIXED_FILE, so file_slot is resolved as a
	 * fixed-file index. cloexec picks the kernel default (O_CLOEXEC) or
	 * IORING_FIXED_FD_NO_CLOEXEC.
	 */
	io_uring_prep_fixed_fd_install(&op->sqe, (int)file_slot, flags);

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
