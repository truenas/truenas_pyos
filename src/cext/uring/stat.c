// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * uring_op_statx -- the file-metadata worker.
 *
 * Takes already-parsed C arguments from the prep_statx stub in uring.c and
 * builds one statx operation: pop an op slot, reference the caller's path
 * bytes, point the SQE at them and at the slot's inline struct statx landing
 * zone, and hand back an opaque UringOp handle. reap() turns the completed
 * struct statx into a truenas_os StatxResult via statx_to_pyobject()
 * (op_build_result in submitreap.c). The op-slot state machine is in op.h.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"

PyObject *
uring_op_statx(UringObject *self, int dirfd, PyObject *path_bytes,
	       int flags, unsigned int mask,
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
	op->tag = URING_TAG_STATX;
	/*
	 * The arm may hold another op type's stale bytes; take the path ref
	 * with the tag, before anything can fail. The SQE points into the
	 * bytes (the kernel getname()s its own copy at submission; an
	 * over-long path is its ENAMETOOLONG at completion) and at the inline
	 * struct statx landing zone, which the kernel fully overwrites on
	 * completion -- stale bytes from a prior op never leak.
	 */
	op->u.statx.path_bytes = Py_NewRef(path_bytes);

	io_uring_prep_statx(&op->sqe, dirfd,
			    PyBytes_AS_STRING(op->u.statx.path_bytes),
			    flags, mask, &op->u.statx.stx);

	op->callback = Py_XNewRef(callback);
	op->private_data = Py_XNewRef(private_data);

	handle = handle_new(self, slot);
	if (handle == NULL) {
		pool_recycle(self, slot);
		return NULL;
	}
	return handle;
}
