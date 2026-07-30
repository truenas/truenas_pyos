// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * submit / reap mechanics for truenas_os.Uring -- the ring's runtime path.
 *
 * uring_submit_batch stages a pre-validated batch into the submission queue and
 * fires one io_uring_submit under the submit lock (the SQ is single-producer, so
 * concurrent submitters from multiple threads serialize here). reap_one turns one
 * completed CQE into a (token, res, result) tuple, runs the fixed-file
 * accounting, recycles the slot, and fires any per-op completion callback. The
 * Python-facing submit()/reap()/cancel() stubs in uring.c do the argument parsing
 * and call these; the op-slot state machine they lean on is in op.h.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"
#include "statx.h"		/* statx_to_pyobject() for the statx op result */
#include "submitreap.h"

/*
 * Build the Python result for a completed op. Returns a new reference, or NULL
 * with an exception set (only on an allocation failure).
 */
static PyObject *
op_build_result(uring_op_t *op, int res)
{
	switch (op->tag) {
	case URING_TAG_OPEN:
		/*
		 * A direct install returns 0, not an fd -- the file went straight
		 * into the registered table. Its handle is the bare slot index.
		 */
		return PyLong_FromUnsignedLong(op->file_slot);
	case URING_TAG_READ:
	case URING_TAG_WRITE:
		return PyLong_FromLong(res);
	case URING_TAG_INSTALL:
		/* The newly installed regular process fd. */
		return PyLong_FromLong(res);
	case URING_TAG_STATX:
		/* The kernel filled the slot's embedded struct statx; wrap it. */
		return statx_to_pyobject(&op->u.statx.stx);
	case URING_TAG_CLOSE:
	default:
		Py_RETURN_NONE;
	}
}

/*
 * Convert one completed op into a plain (token, res, result) tuple. Runs
 * the file-op accounting (per-file counter; release the fixed slot of a failed
 * open or a close), builds the typed result, returns the pool slot, and never
 * raises mid-drain except on an allocation failure building the tuple.
 *
 * Result convention (the driver re-raises / unwraps):
 *   res >= 0, build OK    -> result = the built object (int slot / int / None).
 *   res >= 0, build OOM   -> result = the captured exception instance.
 *   res < 0               -> result = None; res carries -errno.
 */
PyObject *
reap_one(UringObject *self, uring_op_t *op, int res, bool *consumed)
{
	PyObject *result = NULL;
	PyObject *tuple = NULL;
	PyObject *ud_obj = NULL;
	PyObject *res_obj = NULL;
	PyObject *callback = op->callback;	/* stolen below; fired once the */
	PyObject *private_data = op->private_data;	/* tuple is built */
	uint32_t file_slot = op->file_slot;
	bool owns_slot = op->owns_slot;
	bool counted = op->counted_file;
	uint8_t tag = op->tag;
	uint32_t slot = (uint32_t)(op - self->pool);
	uint64_t token = SLOT_IDX_TO_OPID(op->gen, slot);

	/*
	 * Steal the owned callback/passthrough before pool_recycle: op_free_payloads
	 * would Py_CLEAR them, and we need them live to fire after building the tuple.
	 */
	op->callback = NULL;
	op->private_data = NULL;
	*consumed = false;

	if (counted) {
		/*
		 * Release the prep-time charge (symmetric with uring_file_charge). While
		 * the op was in flight live > 0 kept the slot from being closed, so the
		 * decrement lands on the same registration the charge counted. (An op
		 * against an empty slot is never counted, so counted is false above.)
		 */
		self->files[file_slot].live--;
	}

	if (res < 0 && owns_slot && file_slot != URING_NO_SLOT) {
		/* A failed open installed nothing, so its reserved slot returns. */
		uring_file_release(self, file_slot);
	}
	if (tag == URING_TAG_CLOSE && file_slot != URING_NO_SLOT &&
	    self->files[file_slot].in_use) {
		/*
		 * A fixed-file close removes the registration whether or not
		 * filp_close() reported an error, so an installed slot is returned
		 * on both res >= 0 and res < 0 (gating on success would leak it on a
		 * close that fails, e.g. EIO/ENOSPC on some backing stores). But
		 * only when the slot was actually installed: closing a slot that was
		 * never opened -- or a double close -- reaches here with in_use
		 * already false, and releasing it again would push it onto the file
		 * free list a second time, corrupting the list (self-referential
		 * next_free) and handing the same slot to two later opens.
		 */
		uring_file_release(self, file_slot);
	}

	/* Build the result while the slot's landing zones are still live. */
	if (res < 0) {
		result = Py_NewRef(Py_None);
	} else {
		result = op_build_result(op, res);
		if (result == NULL) {
			/* OOM building the result: hand the exception through. */
			result = PyErr_GetRaisedException();
		}
	}

	/* Single-shot: this is the only CQE for this slot, so recycle it now. */
	pool_recycle(self, slot);

	ud_obj = PyLong_FromUnsignedLongLong(token);
	res_obj = PyLong_FromLong(res);
	if (ud_obj == NULL || res_obj == NULL || result == NULL) {
		Py_XDECREF(ud_obj);
		Py_XDECREF(res_obj);
		Py_XDECREF(result);
		Py_XDECREF(callback);		/* no completion to fire; release */
		Py_XDECREF(private_data);
		return NULL;
	}
	tuple = PyTuple_New(3);
	if (tuple == NULL) {
		Py_DECREF(ud_obj);
		Py_DECREF(res_obj);
		Py_DECREF(result);
		Py_XDECREF(callback);
		Py_XDECREF(private_data);
		return NULL;
	}
	PyTuple_SET_ITEM(tuple, 0, ud_obj);	/* steals */
	PyTuple_SET_ITEM(tuple, 1, res_obj);	/* steals */
	PyTuple_SET_ITEM(tuple, 2, result);	/* steals */

	if (callback != NULL) {
		/*
		 * Consuming: hand the completion (+ optional passthrough) to the
		 * callback via vectorcall, then tell the caller not to append it. A
		 * raising callback must not abort the drain (a level-triggered poller
		 * would spin), so report it unraisably. stack[0] is the reserved
		 * vectorcall self-slot (args[-1]).
		 */
		PyObject *stack[3];
		PyObject *cbret = NULL;

		stack[1] = tuple;		/* completion (borrowed) */
		stack[2] = private_data;	/* opaque passthrough */
		cbret = PyObject_Vectorcall(callback, stack + 1,
			(size_t)(private_data != NULL ? 2 : 1) |
				PY_VECTORCALL_ARGUMENTS_OFFSET, NULL);
		if (cbret == NULL) {
			PyErr_FormatUnraisable("Exception ignored in io_uring "
					       "completion callback %R", callback);
		} else {
			Py_DECREF(cbret);
		}
		*consumed = true;
		Py_DECREF(callback);
		Py_XDECREF(private_data);
	}
	return tuple;
}

/*
 * The ring-mechanics half of submit: stage each pre-validated op's SQE into the
 * submission queue and fire one io_uring_submit, all under the submit lock. The
 * SQ is single-producer, so concurrent submitters serialize here; everything the
 * SQ touches (staging, the syscall, the transactional revert) stays inside the
 * lock, while the pool state and inflight counter it also touches are already
 * GIL-serialized.
 *
 * Returns 0 when the batch reached the kernel: the ops are in flight and the
 * caller must consume their handles. A signal that raised during the EINTR retry
 * also returns 0 (the ops are committed) but leaves an exception set for the
 * caller to propagate. Returns -1 when nothing was submitted -- the ring is
 * closed, a slot was not PREPPED (submitted, reclaimed, or a duplicate in the
 * batch), or the SQ could not hold the batch -- with an exception set and every
 * staged slot reverted, so the handles stay reusable.
 */
int
uring_submit_batch(UringObject *self, submit_ent_t *ents, Py_ssize_t n,
		   int linked)
{
	unsigned saved_sqe_tail = 0;
	Py_ssize_t i = 0;
	Py_ssize_t staged = 0;
	int ret = 0;
	PyObject *exc_type = NULL;
	const char *exc_msg = NULL;

	PyMutex_Lock(&self->submit_lock);

	/*
	 * Re-check closed under the lock: close() fences submitters by taking this
	 * same lock and tearing the ring down, so a submit that passed the early
	 * ready check but lost the race bails here, before touching the SQ or the
	 * about-to-be-freed pool.
	 */
	if (self->closed) {
		PyMutex_Unlock(&self->submit_lock);
		PyErr_SetString(PyExc_ValueError, "Uring is closed");
		return -1;
	}

	/*
	 * Save the SQ tail so the transactional revert can discard every SQE the
	 * batch queued; we never submit mid-batch, so restoring the tail un-queues
	 * them.
	 */
	saved_sqe_tail = self->ring.sq.sqe_tail;

	for (i = 0; i < n; i++) {
		uring_op_t *op = &self->pool[ents[i].slot];
		struct io_uring_sqe *sqe = NULL;

		if (op->state != URING_OP_PREPPED) {
			/*
			 * A slot that is not prepared: the handle was already submitted or
			 * reclaimed, or the same handle appears twice in this batch (an
			 * earlier copy already staged it INFLIGHT). Refuse the whole batch.
			 */
			exc_type = PyExc_ValueError;
			exc_msg = "submit() got a handle that is not prepared "
				  "(already submitted, reclaimed, or the same "
				  "handle twice in one batch)";
			break;
		}
		sqe = uring_get_sqe(self);	/* sets BlockingIOError on a full SQ */
		if (sqe == NULL) {
			exc_type = PyExc_BlockingIOError;	/* re-set after revert */
			exc_msg = "submission queue is full: the batch is larger "
				  "than the ring's `entries`";
			break;
		}
		memcpy(sqe, &op->sqe, sizeof(struct io_uring_sqe));
		ents[i].token = SLOT_IDX_TO_OPID(op->gen, ents[i].slot);
		sqe->user_data = ents[i].token;
		if (linked && i != n - 1) {
			sqe->flags |= IOSQE_IO_LINK;
		}
		op->state = URING_OP_INFLIGHT;
		staged++;
	}

	if (exc_type != NULL) {
		/*
		 * Transactional: revert every slot we marked INFLIGHT back to PREPPED
		 * and restore the SQ tail, discarding the queued SQEs. Nothing reached
		 * the kernel, so the handles stay reusable.
		 */
		for (i = 0; i < staged; i++) {
			self->pool[ents[i].slot].state = URING_OP_PREPPED;
		}
		self->ring.sq.sqe_tail = saved_sqe_tail;
		PyMutex_Unlock(&self->submit_lock);
		PyErr_SetString(exc_type, exc_msg);
		return -1;
	}

	/*
	 * Count the batch in flight while still holding the GIL, before
	 * io_uring_submit drops it: once the SQEs reach the kernel a completion can be
	 * reaped on the reaper thread, and reap_one's inflight-- must see the op
	 * already counted. The per-file live counter is charged at prep time.
	 */
	self->inflight += (uint32_t)staged;

	do {
		Py_BEGIN_ALLOW_THREADS
		ret = io_uring_submit(&self->ring);
		Py_END_ALLOW_THREADS
	} while (ret == -EINTR && !PyErr_CheckSignals());

	PyMutex_Unlock(&self->submit_lock);

	/*
	 * The flush committed the batch regardless of `ret` (a non-EINTR submit
	 * error surfaces per-op via the CQE res), so the ops are in flight. If the
	 * retry stopped because a signal handler raised, that exception stays set and
	 * the caller returns it -- the ops still reap, only the tokens are lost.
	 */
	return 0;
}
