// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_OP_H
#define _URING_OP_H

#include "uring.h"

/*
 * The op-slot state machine (see enum uring_op_state in uring.h) and the small
 * per-op argument checks, shared by the prep_* paths (openclose.c, rw.c) and the
 * completion/teardown paths (uring.c). These helpers own the FREE<->PREPPED
 * edges. `static inline` so every TU that uses one gets a definition; unused
 * ones drop with no warning.
 */

/* -- ready check ---------------------------------------------------------- */

static inline int
uring_check_ready(UringObject *self)
{
	if (self->closed || !self->ring_ready) {
		PyErr_SetString(PyExc_ValueError, "Uring is closed");
		return -1;
	}
	return 0;
}

/* -- op-slot pool --------------------------------------------------------- */

/*
 * Release the op's owned refs. Any op may carry a completion callback +
 * passthrough, and an open/statx its path bytes (all cleared unconditionally
 * below). Only a read/write additionally owns its pinned caller buffers (each
 * Py_buffer's view.obj is the strong reference); the statx result and the
 * openat2 how live inline in the pre-allocated slot, and close/fsync own
 * nothing more. Touches Python refcounts (Py_CLEAR / PyBuffer_Release), so it
 * must run with the GIL held.
 */
static inline void
op_free_payloads(uring_op_t *op)
{
	uint32_t i = 0;

	/*
	 * Any op may carry these; reap steals the callback/passthrough before
	 * recycling, so this never frees a ref it is about to fire. Py_CLEAR(NULL)
	 * is a no-op for the common callback-free / path-free op.
	 */
	Py_CLEAR(op->callback);
	Py_CLEAR(op->private_data);
	Py_CLEAR(op->path_bytes);

	if (op->tag == URING_TAG_READ || op->tag == URING_TAG_WRITE) {
		/*
		 * Exactly views[0, nr) are pinned -- uring_op_rw bumps nr only
		 * after each successful pin, so this releases a fully-prepared
		 * op and a mid-pin failure unwind alike. Zeroing nr makes a
		 * second call a no-op.
		 */
		for (i = 0; i < op->u.rw.nr; i++) {
			PyBuffer_Release(&op->u.rw.views[i]);
		}
		op->u.rw.nr = 0;
	}
}

/*
 * Pop a FREE slot from the pool freelist and mark it PREPPED. Returns the slot
 * index, or URING_NO_SLOT when the pool is exhausted (the caller raises
 * BlockingIOError).
 */
static inline uint32_t
pool_alloc(UringObject *self)
{
	uring_op_t *op = NULL;
	uint32_t slot = 0;

	if (self->pool_free != URING_NO_SLOT) {
		/* Reuse a recycled slot (already faulted in). */
		slot = self->pool_free;
		self->pool_free = self->pool[slot].next_free;
	} else if (self->pool_hi < self->nr_pool) {
		/*
		 * Hand out a fresh, never-touched slot; the writes below fault it
		 * in. Slots >= pool_hi stay demand-zero (unbacked), so RSS tracks
		 * peak concurrency, not `entries`.
		 */
		slot = self->pool_hi++;
	} else {
		return URING_NO_SLOT;
	}

	op = &self->pool[slot];
	op->next_free = URING_NO_SLOT;
	op->state = URING_OP_PREPPED;
	op->tag = 0;
	op->callback = NULL;		/* already NULL (calloc / op_free_payloads); */
	op->private_data = NULL;	/* kept explicit so "op owns these" is local */
	op->path_bytes = NULL;
	op->gen++;	/* new instance on this slot (see SLOT_IDX_TO_OPID); persists
			 * across pool_recycle so consecutive uses never share an id */
	/*
	 * The slot's SQE is reused across ops, so clear the fields the prep_*
	 * helpers do not set themselves (flags, ...).
	 * This is exactly what io_uring_get_sqe() does for a ring SQE; without
	 * it a prior op's flags would leak into the next prep.
	 *
	 * The payload union `u` is not reset here: op_free_payloads only touches a
	 * read/write's pinned buffers, gated on u.rw.nr, which uring_op_rw()
	 * zeroes (then counts up) before any failure path can free the slot.
	 * open/statx paths and the statx result are inline and need no cleanup.
	 * The caller fills the rest of the SQE.
	 */
	io_uring_initialize_sqe(&op->sqe);
	return slot;
}

/*
 * Release an op's payloads and return its slot to the pool free list.
 */
static inline void
pool_recycle(UringObject *self, uint32_t slot)
{
	uring_op_t *op = &self->pool[slot];

	op_free_payloads(op);
	op->state = URING_OP_FREE;
	op->next_free = self->pool_free;
	self->pool_free = slot;
}

/* -- argument helpers ----------------------------------------------------- */

/*
 * Map a METH_FASTCALL|METH_KEYWORDS call onto a flat positional slot array by
 * name -- the fast keyword path for prep_openat2 / prep_statx. `params` is the
 * positional-order parameter-name list; on return slots[i] holds the argument
 * for parameter i (borrowed) or NULL if not supplied. Reads the small `kwnames`
 * tuple CPython hands us and takes each keyword value from args[nargs + k];
 * nothing is materialised. Rejects too-many-positional / duplicate / unknown
 * keywords with CPython's own wording. Returns 0, or -1 with an exception set;
 * the caller enforces which slots are required.
 *
 * The common positional call skips this (kwnames == NULL fast path in the
 * caller) and reads args[] directly; this runs only when keywords are present.
 */
static inline int
map_kwargs(const char *funcname, PyObject *const *args, Py_ssize_t nargs,
	   PyObject *kwnames, const char *const *params, Py_ssize_t nparams,
	   PyObject **slots)
{
	Py_ssize_t i = 0;
	Py_ssize_t k = 0;
	Py_ssize_t nkw = 0;

	if (nargs > nparams) {
		PyErr_Format(PyExc_TypeError,
			     "%s() takes at most %zd positional arguments",
			     funcname, nparams);
		return -1;
	}
	for (i = 0; i < nargs; i++) {
		slots[i] = args[i];
	}
	if (kwnames == NULL) {
		return 0;
	}
	nkw = PyTuple_GET_SIZE(kwnames);
	for (k = 0; k < nkw; k++) {
		PyObject *name = PyTuple_GET_ITEM(kwnames, k);	/* borrowed */

		for (i = 0; i < nparams; i++) {
			if (PyUnicode_CompareWithASCIIString(name, params[i]) == 0) {
				if (slots[i] != NULL) {
					PyErr_Format(PyExc_TypeError,
						     "%s() got multiple values for "
						     "argument '%s'", funcname,
						     params[i]);
					return -1;
				}
				slots[i] = args[nargs + k];
				break;
			}
		}
		if (i == nparams) {
			PyErr_Format(PyExc_TypeError,
				     "%s() got an unexpected keyword argument "
				     "'%U'", funcname, name);
			return -1;
		}
	}
	return 0;
}

/* -- op workers ----------------------------------------------------------- */

/*
 * Allocate an opaque UringOp handle for a prepared slot (defined in uring.c
 * with the UringOp type). The op workers below call it; on NULL the caller
 * releases the slot.
 */
PyObject *handle_new(UringObject *self, uint32_t slot);

/*
 * Op workers -- build one prepared operation from already-parsed C arguments
 * and return its opaque UringOp handle (or NULL with an exception set). The
 * method-table stubs in uring.c do the Python-level parsing/validation and then
 * call these; the workers own the op-slot mechanics (pool_alloc, the SQE fill,
 * handle_new). Split across openclose.c (open/close), rw.c (read/write),
 * fsync.c (fsync) and stat.c (statx).
 *
 * uring_op_openat2 / uring_op_statx take `path_bytes` (borrowed) -- the stub's
 * PyUnicode_FSConverter result, guaranteed a NUL-terminated PyBytes with no
 * embedded NUL -- and store an owned ref on the op so the SQE may point at its
 * internal buffer; uring_op_rw pins each buffer in `buffers` itself and owns
 * the Py_buffers for the op's lifetime. All five also take an optional
 * `callback` / `private_data` (borrowed) and store owned refs on the op.
 */
PyObject *uring_op_openat2(UringObject *self, int dirfd, PyObject *path_bytes,
			   int flags, int mode, unsigned long long resolve,
			   PyObject *callback, PyObject *private_data);
PyObject *uring_op_close(UringObject *self, int fd,
			 PyObject *callback, PyObject *private_data);
PyObject *uring_op_rw(UringObject *self, int fd, PyObject *buffers,
		      unsigned long long offset, int flags, bool is_write,
		      PyObject *callback, PyObject *private_data);
PyObject *uring_op_fsync(UringObject *self, int fd, int datasync,
			 unsigned long long offset, unsigned int length,
			 PyObject *callback, PyObject *private_data);
PyObject *uring_op_statx(UringObject *self, int dirfd, PyObject *path_bytes,
			 int flags, unsigned int mask,
			 PyObject *callback, PyObject *private_data);

#endif /* _URING_OP_H */
