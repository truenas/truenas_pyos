// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _URING_OP_H
#define _URING_OP_H

#include "uring.h"

/*
 * The op-slot state machine (see enum uring_op_state in uring.h) and the small
 * per-op argument checks, shared by the prep_* paths (openclose.c, rw.c) and the
 * completion/teardown paths (uring.c). These helpers own the FREE<->PREPPED
 * edges and the fixed-file table bookkeeping. `static inline` so every TU that
 * uses one gets a definition; unused ones drop with no warning.
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

/* -- fixed-file table ----------------------------------------------------- */

static inline uint32_t
uring_file_alloc(UringObject *self)
{
	uring_file_t *f = NULL;
	uint32_t slot = 0;

	slot = self->file_free;
	if (slot == URING_NO_SLOT) {
		return URING_NO_SLOT;
	}

	f = &self->files[slot];
	self->file_free = f->next_free;
	f->in_use = true;
	f->close_pending = false;
	f->live = 0;
	f->next_free = URING_NO_SLOT;
	return slot;
}

static inline void
uring_file_release(UringObject *self, uint32_t slot)
{
	uring_file_t *f = &self->files[slot];

	f->in_use = false;
	f->close_pending = false;
	f->live = 0;
	f->next_free = self->file_free;
	self->file_free = slot;
}

/*
 * Charge a prepared op against its fixed-file slot: bump the slot's live count so
 * a later prep_close sees it (whether still prepared or already in flight) and
 * refuses to unregister the slot from under it. Only a real registration is
 * charged -- an op against an empty (never-opened / already-closed) in-range slot
 * is EBADF-doomed and references no registration, so it is left uncounted. The
 * charge is released symmetrically at reap / drop, gated on op->counted_file; a
 * charged op keeps live > 0, which makes prep_close refuse, so the slot stays put
 * while the op is in flight.
 */
static inline void
uring_file_charge(UringObject *self, uring_op_t *op)
{
	uint32_t fs = op->file_slot;

	if (fs != URING_NO_SLOT && fs < self->nr_files && self->files[fs].in_use) {
		self->files[fs].live++;
		op->counted_file = true;
	}
}

/*
 * Reject preparing a (non-close) op on a slot that already has a close prepared
 * or in flight: the close is going to unregister the slot, so nothing further may
 * target it until the close reaps. O(1) -- the reverse of prep_close's live > 0
 * guard; together they keep a close and any other op off the same slot in either
 * prep order (an unordered close batched with a read/write could otherwise race).
 * Returns 0 to proceed, or -1 with a BlockingIOError set.
 */
static inline int
uring_file_check_open(UringObject *self, uint32_t file_slot)
{
	if (self->files[file_slot].in_use && self->files[file_slot].close_pending) {
		PyErr_SetString(PyExc_BlockingIOError,
				"a close is already prepared for this file slot; no "
				"further operation on the slot may be prepared until "
				"the close reaps");
		return -1;
	}
	return 0;
}

/* -- op-slot pool --------------------------------------------------------- */

/*
 * Release the op's owned refs. Any op may carry a completion callback +
 * passthrough (cleared unconditionally below). Only a read/write additionally
 * owns the pinned caller buffer and its strong reference; open/statx keep their
 * path and the statx result inline in the pre-allocated slot, and close owns
 * nothing more. Touches Python refcounts (Py_CLEAR / PyBuffer_Release), so it
 * must run with the GIL held.
 */
static inline void
op_free_payloads(uring_op_t *op)
{
	/*
	 * Any op may carry these; reap steals them before recycling, so this never
	 * frees a ref it is about to fire. Py_CLEAR(NULL) is a no-op for the common
	 * callback-free op.
	 */
	Py_CLEAR(op->callback);
	Py_CLEAR(op->private_data);

	if (op->tag == URING_TAG_READ || op->tag == URING_TAG_WRITE) {
		/*
		 * buf_obj is non-NULL exactly when the view is pinned -- uring_op_rw
		 * sets them together and this clears them together -- so buf_obj is the
		 * single "is there a pin?" flag. Py_CLEAR makes a second call a no-op.
		 */
		if (op->u.rw.buf_obj != NULL) {
			PyBuffer_Release(&op->u.rw.view);
			Py_CLEAR(op->u.rw.buf_obj);
		}
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
	op->file_slot = URING_NO_SLOT;
	op->owns_slot = false;
	op->counted_file = false;
	op->callback = NULL;		/* already NULL (calloc / op_free_payloads); */
	op->private_data = NULL;	/* kept explicit so "op owns these" is local */
	op->gen++;	/* new instance on this slot (see SLOT_IDX_TO_OPID); persists
			 * across pool_recycle so consecutive uses never share an id */
	/*
	 * The slot's SQE is reused across ops, so clear the fields the prep_*
	 * helpers do not set themselves (flags, file_index, ...).
	 * This is exactly what io_uring_get_sqe() does for a ring SQE; without
	 * it a prior op's flags/file_index would leak into the next prep.
	 *
	 * The payload union `u` is not reset here: op_free_payloads only touches a
	 * read/write's pinned buffer, which uring_op_rw() sets before any failure
	 * path can free it. open/statx paths and the statx result are inline and
	 * need no cleanup. The caller fills the rest of the SQE.
	 */
	io_uring_initialize_sqe(&op->sqe);
	return slot;
}

/*
 * Release an op's payloads and return its slot to the pool free list. Does NOT
 * touch the fixed-file table -- reap_one does that accounting itself, because a
 * successful open must keep the slot it installed while a dropped prep must
 * hand it back.
 */
static inline void
pool_recycle(UringObject *self, uint32_t slot)
{
	uring_op_t *op = &self->pool[slot];

	op_free_payloads(op);
	op->state = URING_OP_FREE;
	op->file_slot = URING_NO_SLOT;
	op->owns_slot = false;
	op->next_free = self->pool_free;
	self->pool_free = slot;
}

/*
 * Fully reclaim a prepared-but-never-submitted op: hand back the fixed-file
 * slot it reserved (an open) as well as its payloads. Used by the prep_*
 * failure paths and by a dropped handle's destructor.
 */
static inline void
pool_release(UringObject *self, uint32_t slot)
{
	uring_op_t *op = &self->pool[slot];

	if (op->file_slot != URING_NO_SLOT) {
		/*
		 * Undo the prep-time fixed-file charge, symmetric with reap_one: a
		 * dropped-before-submit op releases its live count and, if it was the
		 * slot's pending close, clears that -- returning the slot to a clean
		 * state. An open (owns_slot) additionally hands its reserved slot back.
		 */
		if (op->counted_file) {
			self->files[op->file_slot].live--;
		}
		if (op->tag == URING_TAG_CLOSE && self->files[op->file_slot].in_use) {
			self->files[op->file_slot].close_pending = false;
		}
		if (op->owns_slot) {
			uring_file_release(self, op->file_slot);
		}
	}
	pool_recycle(self, slot);
}

/* -- argument helpers ----------------------------------------------------- */

/*
 * Validate a fixed-file slot: a range check only. A slot that is in range but
 * empty (never opened, or already closed) yields the kernel's EBADF at
 * completion -- bare-slot discipline, exactly like a stale fd.
 */
static inline int
check_file(UringObject *self, PyObject *obj, uint32_t *slot)
{
	long v = 0;

	if (!PyLong_Check(obj)) {
		PyErr_Format(PyExc_TypeError,
			     "file must be an int slot, not %.200s",
			     Py_TYPE(obj)->tp_name);
		return -1;
	}
	v = PyLong_AsLong(obj);
	if (v == -1 && PyErr_Occurred()) {
		return -1;
	}
	if (v < 0 || (uint64_t)v >= self->nr_files) {	/* full-width: no truncation */
		PyErr_Format(PyExc_ValueError,
			     "file slot out of range (0..%u), got %ld",
			     self->nr_files - 1, v);
		return -1;
	}

	*slot = (uint32_t)v;
	return 0;
}

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
 * handle_new). Split across openclose.c (open/close/install), rw.c (read/write)
 * and stat.c (statx).
 *
 * uring_op_openat2 / uring_op_statx copy `path` (borrowed -- the stub keeps its
 * FSConverter result alive across the call); uring_op_rw pins `buf` itself and
 * owns the Py_buffer for the op's lifetime. All five also take an optional
 * `callback` / `private_data` (borrowed) and store owned refs on the op.
 */
PyObject *uring_op_openat2(UringObject *self, int dirfd, const char *path,
			   Py_ssize_t path_len, int flags, int mode,
			   unsigned long long resolve,
			   PyObject *callback, PyObject *private_data);
PyObject *uring_op_close(UringObject *self, uint32_t file_slot,
			 PyObject *callback, PyObject *private_data);
PyObject *uring_op_fixed_fd_install(UringObject *self, uint32_t file_slot,
				    int cloexec,
				    PyObject *callback, PyObject *private_data);
PyObject *uring_op_rw(UringObject *self, uint32_t file_slot, PyObject *buf,
		      unsigned long long offset, bool is_write,
		      PyObject *callback, PyObject *private_data);
PyObject *uring_op_statx(UringObject *self, int dirfd, const char *path,
			 Py_ssize_t path_len, int flags, unsigned int mask,
			 PyObject *callback, PyObject *private_data);

#endif /* _URING_OP_H */
