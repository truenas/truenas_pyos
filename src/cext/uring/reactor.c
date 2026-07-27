// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * The reactor: ring lifecycle, op table, fixed-file table, and the reap loop.
 *
 * Threading model: one ring, one owning thread, which is the thread running
 * the asyncio loop the reactor is attached to.  Submission happens inline on
 * that thread; completions are reaped in a loop.add_reader() callback, which
 * also runs on that thread with the GIL held.  So Futures are only ever
 * settled from the loop thread and no PyGILState_Ensure or
 * call_soon_threadsafe is needed anywhere.
 *
 * The ring fd is directly pollable -- io_uring_poll() reports EPOLLIN when
 * __io_cqring_events_user() or io_has_work() is true -- so no eventfd is
 * registered.  Two consequences the reap path must respect: add_reader is
 * level-triggered, so the CQ must be drained to empty; and the kernel warns
 * in io_uring_poll() that it does not flush the overflow list, so EPOLLIN
 * with an empty CQ ring is normal and not an error.
 */

#include <Python.h>
#include "common/includes.h"
#include "reactor.h"
#include "statx.h"
#include "ops.h"

/*
 * Interned method names for the completion path.
 *
 * Cached rather than re-interned per completion, and used with the
 * OneArg/NoArgs call forms deliberately: PyObject_CallMethod(f, "set_result",
 * "O", x) builds its argument tuple with Py_BuildValue("O", x), which returns
 * x *itself* -- and a StatxResult is a PyStructSequence, i.e. a tuple
 * subclass, so it was splatted into 32 positional arguments.
 */
static PyObject *s_set_result;
static PyObject *s_set_exception;
static PyObject *s_cancelled;
static PyObject *s_create_future;

static int
init_interned_names(void)
{
	if (s_set_result != NULL) {
		return 0;
	}
	s_set_result = PyUnicode_InternFromString("set_result");
	s_set_exception = PyUnicode_InternFromString("set_exception");
	s_cancelled = PyUnicode_InternFromString("cancelled");
	s_create_future = PyUnicode_InternFromString("create_future");

	if (s_set_result == NULL || s_set_exception == NULL ||
	    s_cancelled == NULL || s_create_future == NULL) {
		return -1;
	}
	return 0;
}

PyObject *
uring_create_future(PyObject *loop)
{
	return PyObject_CallMethodNoArgs(loop, s_create_future);
}

/* -- errors --------------------------------------------------------------- */

PyObject *
uring_set_error(int err)
{
	/*
	 * CQE results are negative errno; PyErr_SetFromErrno reads the global
	 * errno, so set it rather than inventing a message.
	 */
	errno = err < 0 ? -err : err;
	return PyErr_SetFromErrno(PyExc_OSError);
}

int
uring_check_ready(ReactorObject *self)
{
	if (self->closed || !self->ring_ready) {
		PyErr_SetString(PyExc_ValueError, "Reactor is closed");
		return -1;
	}
	if (!self->attached) {
		PyErr_SetString(PyExc_RuntimeError,
				"Reactor is not attached to an event loop; "
				"call attach(loop) first");
		return -1;
	}
	return 0;
}

/* -- op table ------------------------------------------------------------- */

static void
op_free_payloads(uring_op_t *op)
{
	if (op->has_view) {
		PyBuffer_Release(&op->view);
		op->has_view = false;
	}
	Py_CLEAR(op->buf_obj);
	Py_CLEAR(op->future);

	PyMem_RawFree(op->path);
	op->path = NULL;
	PyMem_RawFree(op->how);
	op->how = NULL;
	PyMem_RawFree(op->stx);
	op->stx = NULL;
}

uint32_t
uring_op_alloc(ReactorObject *self, uint8_t tag)
{
	uring_op_t *op = NULL;
	uint32_t slot = 0;

	slot = self->op_free;
	if (slot == URING_NO_SLOT) {
		return URING_NO_SLOT;
	}

	op = &self->ops[slot];
	self->op_free = op->next_free;

	op->tag = tag;
	op->state = URING_OP_INFLIGHT;
	op->next_free = URING_NO_SLOT;
	op->file_slot = URING_NO_SLOT;
	op->owns_slot = false;
	return slot;
}

void
uring_op_release(ReactorObject *self, uint32_t slot)
{
	uring_op_t *op = &self->ops[slot];

	op_free_payloads(op);

	/*
	 * Bump before the waiter is settled so a re-entrant submission that
	 * reuses this slot cannot be confused with the occupant that just
	 * completed.
	 */
	op->gen++;
	op->state = URING_OP_FREE;
	op->tag = 0;
	op->file_slot = URING_NO_SLOT;
	op->owns_slot = false;
	op->next_free = self->op_free;
	self->op_free = slot;
}

/* -- fixed-file table ----------------------------------------------------- */

uint32_t
uring_file_alloc(ReactorObject *self)
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
	f->ops = 0;
	f->next_free = URING_NO_SLOT;
	return slot;
}

void
uring_file_release(ReactorObject *self, uint32_t slot)
{
	uring_file_t *f = &self->files[slot];

	f->in_use = false;
	f->close_pending = false;
	f->ops = 0;
	f->gen++;
	f->next_free = self->file_free;
	self->file_free = slot;
}

/* -- submission ----------------------------------------------------------- */

struct io_uring_sqe *
uring_get_sqe(ReactorObject *self)
{
	struct io_uring_sqe *sqe = NULL;
	int ret = 0;

	sqe = io_uring_get_sqe(&self->ring);
	if (sqe != NULL) {
		return sqe;
	}

	/* SQ momentarily full: flush what is queued and try once more. */
	Py_BEGIN_ALLOW_THREADS
	ret = io_uring_submit(&self->ring);
	Py_END_ALLOW_THREADS

	if (ret < 0) {
		uring_set_error(ret);
		return NULL;
	}

	sqe = io_uring_get_sqe(&self->ring);
	if (sqe == NULL) {
		PyErr_SetString(PyExc_BlockingIOError,
				"submission queue is full");
	}
	return sqe;
}

/* -- completion ----------------------------------------------------------- */

/*
 * Build the Python result for a completed op. Returns a new reference, or
 * NULL with an exception set (which becomes the Future's exception).
 */
static PyObject *
op_build_result(ReactorObject *self, uring_op_t *op, int res)
{
	FixedFileObject *file = NULL;

	switch (op->tag) {
	case URING_TAG_OPEN:
		/*
		 * An explicit-index install returns 0, not an fd -- the file
		 * went straight into the registered table and no process fd
		 * was ever materialised.
		 */
		if (op->file_slot == URING_NO_SLOT) {
			PyErr_SetString(PyExc_SystemError,
					"open completed with no reserved file slot");
			return NULL;
		}
		file = PyObject_GC_New(FixedFileObject, &FixedFileType);
		if (file == NULL) {
			return NULL;
		}
		file->reactor = Py_NewRef((PyObject *)self);
		file->slot = op->file_slot;
		file->gen = self->files[op->file_slot].gen;
		file->closed = false;
		PyObject_GC_Track(file);
		return (PyObject *)file;

	case URING_TAG_READ:
	case URING_TAG_WRITE:
		return PyLong_FromLong(res);

	case URING_TAG_STATX:
		/*
		 * Direct call into truenas_os's own packing -- same shared
		 * object, so no capsule and no duplicated field list.
		 */
		return statx_to_pyobject(op->stx);

	case URING_TAG_FSYNC:
	case URING_TAG_CLOSE:
	default:
		Py_RETURN_NONE;
	}
}

/*
 * Settle one completion. Never propagates an exception to the caller: a
 * per-op failure must not kill the reap loop, so anything unexpected is
 * routed to the Future or, failing that, to the loop's exception handler.
 */
static void
op_complete(ReactorObject *self, uint32_t slot, int res)
{
	uring_op_t *op = &self->ops[slot];
	PyObject *future = NULL;
	PyObject *result = NULL;
	PyObject *exc = NULL;
	PyObject *ret = NULL;
	uint32_t file_slot = URING_NO_SLOT;
	bool owns_slot = false;
	uint8_t tag = op->tag;

	file_slot = op->file_slot;
	owns_slot = op->owns_slot;

	/* Take the waiter out before any bookkeeping that could re-enter. */
	future = op->future;
	op->future = NULL;

	if (file_slot != URING_NO_SLOT && self->files[file_slot].in_use) {
		if (self->files[file_slot].ops > 0) {
			self->files[file_slot].ops--;
		}
	}

	if (res < 0) {
		/*
		 * An open that failed installed nothing, so the slot it
		 * reserved goes back to the free list.
		 */
		if (owns_slot && file_slot != URING_NO_SLOT) {
			uring_file_release(self, file_slot);
		}
	} else if (tag == URING_TAG_CLOSE && file_slot != URING_NO_SLOT) {
		uring_file_release(self, file_slot);
	}

	if (future == NULL) {
		/* Orphaned: buffers were held only until this CQE. */
		uring_op_release(self, slot);
		return;
	}

	if (res < 0) {
		uring_set_error(res);
	} else {
		result = op_build_result(self, op, res);
	}

	/*
	 * Capture the exception *before* releasing the slot. Releasing runs
	 * Py_CLEAR and PyBuffer_Release, i.e. arbitrary destructors, any of
	 * which may clobber the error indicator -- which previously left
	 * set_exception() being handed None ("invalid exception object") and
	 * the Future never resolved.
	 */
	if (result == NULL) {
		exc = PyErr_GetRaisedException();
	}

	/*
	 * Free the slot and bump the generation *before* settling, so a
	 * callback may legally reuse this slot immediately.
	 */
	uring_op_release(self, slot);

	{
		/*
		 * A cancelled Future rejects set_result/set_exception with
		 * InvalidStateError, so check first. The awaiting coroutine is
		 * already gone; the completion is simply discarded. Buffers
		 * were held until this CQE regardless, which is the point.
		 */
		PyObject *cancelled = PyObject_CallMethodNoArgs(future, s_cancelled);
		int is_cancelled = 0;

		if (cancelled == NULL) {
			PyErr_Clear();
		} else {
			is_cancelled = PyObject_IsTrue(cancelled);
			Py_DECREF(cancelled);
		}

		if (is_cancelled) {
			Py_XDECREF(result);
			Py_XDECREF(exc);
			Py_DECREF(future);
			return;
		}
	}

	if (result == NULL) {
		if (exc == NULL) {
			/* Should not happen; never hand set_exception a None. */
			exc = PyObject_CallFunction(PyExc_OSError, "is", EIO,
						    "operation failed with no "
						    "exception set");
		}
		if (exc != NULL) {
			ret = PyObject_CallMethodOneArg(future, s_set_exception,
							exc);
			Py_DECREF(exc);
			exc = NULL;
		}
	} else {
		ret = PyObject_CallMethodOneArg(future, s_set_result, result);
		Py_DECREF(result);
	}

	Py_XDECREF(ret);
	if (ret == NULL) {
		/* InvalidStateError and friends: report, do not propagate. */
		PyErr_WriteUnraisable(future);
	}
	Py_DECREF(future);
}

PyDoc_STRVAR(Reactor_reap__doc__,
"_reap()\n"
"--\n\n"
"Drain the completion queue and settle the corresponding Futures.\n\n"
"Registered with loop.add_reader() by attach(). Because add_reader is\n"
"level-triggered this must drain to empty, and because io_uring_poll() does\n"
"not flush the overflow list a wakeup with zero completions is normal.\n\n"
"Returns\n"
"-------\n"
"int\n"
"    Number of completions processed.\n");

static PyObject *
Reactor_reap(ReactorObject *self, PyObject *Py_UNUSED(ignored))
{
	struct io_uring_cqe *cqe = NULL;
	unsigned int count = 0;

	if (self->closed || !self->ring_ready) {
		return PyLong_FromLong(0);
	}

	while (io_uring_peek_cqe(&self->ring, &cqe) == 0) {
		uint64_t ud = io_uring_cqe_get_data64(cqe);
		int res = cqe->res;
		uint32_t slot = URING_UD_SLOT(ud);
		uint32_t gen = URING_UD_GEN(ud);
		uint8_t tag = URING_UD_TAG(ud);

		io_uring_cqe_seen(&self->ring, cqe);
		count++;

		if (tag == URING_TAG_CANCEL) {
			/* Cancel results are advisory; nothing waits on them. */
			continue;
		}

		if (self->inflight > 0) {
			self->inflight--;
		}

		if (slot >= self->nr_ops) {
			continue;
		}
		if (self->ops[slot].state == URING_OP_FREE ||
		    self->ops[slot].gen != gen) {
			/* Stale completion for a recycled slot: inert. */
			continue;
		}

		op_complete(self, slot, res);
	}

	return PyLong_FromUnsignedLong(count);
}

/* -- teardown ------------------------------------------------------------- */

/*
 * Orphan every in-flight op, ask the kernel to cancel them, and wait until
 * every CQE has reaped.
 *
 * Returns 0 when the ring is fully drained. On failure returns -1, and the
 * caller must LEAK the tables rather than free them: the kernel may still
 * hold pointers into buffers those entries own.
 */
static int
reactor_drain(ReactorObject *self)
{
	struct io_uring_sqe *sqe = NULL;
	struct io_uring_cqe *cqe = NULL;
	uint32_t i = 0;
	int ret = 0;

	if (!self->ring_ready) {
		return 0;
	}

	for (i = 0; i < self->nr_ops; i++) {
		if (self->ops[i].state == URING_OP_INFLIGHT) {
			self->ops[i].state = URING_OP_ORPHANED;
			Py_CLEAR(self->ops[i].future);
		}
	}

	if (self->inflight == 0) {
		return 0;
	}

	sqe = io_uring_get_sqe(&self->ring);
	if (sqe != NULL) {
		io_uring_prep_cancel64(sqe, 0, IORING_ASYNC_CANCEL_ANY);
		io_uring_sqe_set_data64(sqe, URING_UD(URING_TAG_CANCEL, 0, 0));
		Py_BEGIN_ALLOW_THREADS
		io_uring_submit(&self->ring);
		Py_END_ALLOW_THREADS
	}

	while (self->inflight > 0) {
		Py_BEGIN_ALLOW_THREADS
		ret = io_uring_wait_cqe(&self->ring, &cqe);
		Py_END_ALLOW_THREADS

		if (ret < 0) {
			return -1;
		}

		{
			uint64_t ud = io_uring_cqe_get_data64(cqe);
			uint32_t slot = URING_UD_SLOT(ud);
			uint8_t tag = URING_UD_TAG(ud);

			io_uring_cqe_seen(&self->ring, cqe);

			if (tag == URING_TAG_CANCEL) {
				continue;
			}
			self->inflight--;
			if (slot < self->nr_ops &&
			    self->ops[slot].state != URING_OP_FREE) {
				uring_op_release(self, slot);
			}
		}
	}

	return 0;
}

static int
reactor_detach(ReactorObject *self)
{
	PyObject *ret = NULL;

	if (!self->attached) {
		return 0;
	}

	self->attached = false;

	if (self->loop != NULL) {
		ret = PyObject_CallMethod(self->loop, "remove_reader", "i",
					  self->ring_fd);
		if (ret == NULL) {
			/*
			 * A closed loop raises here; that is not a reason to
			 * fail teardown.
			 */
			PyErr_Clear();
		}
		Py_XDECREF(ret);
	}

	Py_CLEAR(self->loop);
	Py_CLEAR(self->reap_cb);
	return 0;
}

static void
reactor_shutdown(ReactorObject *self)
{
	bool drained = false;

	if (self->closed) {
		return;
	}
	self->closed = true;

	(void)reactor_detach(self);

	drained = (reactor_drain(self) == 0);

	if (self->ring_ready) {
		io_uring_queue_exit(&self->ring);
		self->ring_ready = false;
		self->ring_fd = -1;
	}

	if (drained) {
		if (self->ops != NULL) {
			uint32_t i;
			for (i = 0; i < self->nr_ops; i++) {
				op_free_payloads(&self->ops[i]);
			}
			PyMem_RawFree(self->ops);
		}
		PyMem_RawFree(self->files);
	}
	/*
	 * else: deliberately leaked. The drain failed, so the kernel may
	 * still write into buffers these entries own; freeing them would be
	 * a use-after-free in the kernel's hands.
	 */
	self->ops = NULL;
	self->files = NULL;
	self->nr_ops = 0;
	self->nr_files = 0;
}

/* -- Reactor type --------------------------------------------------------- */

PyDoc_STRVAR(reactor__doc__,
"Reactor(entries=256, files=1024, cq_entries=0)\n"
"--\n\n"
"An io_uring filesystem reactor bound to one asyncio event loop.\n\n"
"One ring, one owning thread: the thread that calls attach() must be the\n"
"thread running the loop, and it is the only thread that may submit.\n\n"
"The ring is deliberately created without IORING_SETUP_SINGLE_ISSUER (and\n"
"therefore without IORING_SETUP_DEFER_TASKRUN). Those flags set the kernel's\n"
"submitter_task, after which io_uring_register() from any other task fails\n"
"with EEXIST -- which would lock the credential broker out of registering\n"
"personalities.\n\n"
"Parameters\n"
"----------\n"
"entries : int\n"
"    Submission queue depth. Rounded up to a power of two by the kernel.\n"
"files : int\n"
"    Size of the registered (fixed) file table.\n"
"cq_entries : int\n"
"    Completion queue depth. 0 selects the kernel default (2 * entries).\n\n"
"Raises\n"
"------\n"
"OSError\n"
"    If io_uring is unavailable, disabled (kernel.io_uring_disabled) or\n"
"    blocked by seccomp.\n");

static PyObject *
Reactor_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = {"entries", "files", "cq_entries", NULL};
	ReactorObject *self = NULL;
	struct io_uring_params params;
	unsigned int entries = 256;
	unsigned int files = 1024;
	unsigned int cq_entries = 0;
	uint32_t i = 0;
	int ret = 0;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|III:Reactor", kwlist,
					 &entries, &files, &cq_entries)) {
		return NULL;
	}

	if (entries == 0) {
		PyErr_SetString(PyExc_ValueError, "entries must be non-zero");
		return NULL;
	}
	if (entries > URING_MAX_OPS) {
		PyErr_Format(PyExc_ValueError,
			     "entries must be <= %u (the user_data slot field "
			     "is 24 bits)", URING_MAX_OPS);
		return NULL;
	}
	if (files == 0) {
		PyErr_SetString(PyExc_ValueError, "files must be non-zero");
		return NULL;
	}

	self = (ReactorObject *)type->tp_alloc(type, 0);
	if (self == NULL) {
		return NULL;
	}

	self->ring_fd = -1;
	self->op_free = URING_NO_SLOT;
	self->file_free = URING_NO_SLOT;

	self->ops = PyMem_RawCalloc(entries, sizeof(uring_op_t));
	self->files = PyMem_RawCalloc(files, sizeof(uring_file_t));
	if (self->ops == NULL || self->files == NULL) {
		PyErr_NoMemory();
		goto fail;
	}
	self->nr_ops = entries;
	self->nr_files = files;

	/* Build both free lists in ascending order. */
	for (i = entries; i > 0; i--) {
		self->ops[i - 1].next_free = self->op_free;
		self->ops[i - 1].state = URING_OP_FREE;
		self->ops[i - 1].file_slot = URING_NO_SLOT;
		self->op_free = i - 1;
	}
	for (i = files; i > 0; i--) {
		self->files[i - 1].next_free = self->file_free;
		self->file_free = i - 1;
	}

	memset(&params, 0, sizeof(params));
	params.flags = IORING_SETUP_CLAMP;
	if (cq_entries > 0) {
		params.flags |= IORING_SETUP_CQSIZE;
		params.cq_entries = cq_entries;
	}

	Py_BEGIN_ALLOW_THREADS
	ret = io_uring_queue_init_params(entries, &self->ring, &params);
	Py_END_ALLOW_THREADS

	if (ret < 0) {
		uring_set_error(ret);
		goto fail;
	}
	self->ring_ready = true;
	self->ring_fd = self->ring.ring_fd;

	/*
	 * A sparse table with no registered alloc range: files are installed
	 * at explicit indices this module hands out, never with
	 * IORING_FILE_INDEX_ALLOC.
	 */
	Py_BEGIN_ALLOW_THREADS
	ret = io_uring_register_files_sparse(&self->ring, files);
	Py_END_ALLOW_THREADS

	if (ret < 0) {
		uring_set_error(ret);
		goto fail;
	}

	return (PyObject *)self;

fail:
	if (self->ring_ready) {
		io_uring_queue_exit(&self->ring);
		self->ring_ready = false;
	}
	PyMem_RawFree(self->ops);
	PyMem_RawFree(self->files);
	self->ops = NULL;
	self->files = NULL;
	Py_DECREF(self);
	return NULL;
}

static int
Reactor_traverse(ReactorObject *self, visitproc visit, void *arg)
{
	uint32_t i = 0;

	Py_VISIT(self->loop);
	Py_VISIT(self->reap_cb);

	for (i = 0; i < self->nr_ops; i++) {
		Py_VISIT(self->ops[i].future);
		Py_VISIT(self->ops[i].buf_obj);
	}
	return 0;
}

static int
Reactor_clear(ReactorObject *self)
{
	uint32_t i = 0;

	Py_CLEAR(self->loop);
	Py_CLEAR(self->reap_cb);

	/*
	 * Break cycles through the Futures, but do NOT touch buffers: an
	 * in-flight op's buffer is still kernel-visible, and nothing in a
	 * buffer refers back to the reactor, so it cannot be part of a cycle
	 * anyway. Buffers are released by reactor_shutdown() after the drain.
	 */
	for (i = 0; i < self->nr_ops; i++) {
		Py_CLEAR(self->ops[i].future);
		if (self->ops[i].state == URING_OP_INFLIGHT) {
			self->ops[i].state = URING_OP_ORPHANED;
		}
	}
	return 0;
}

static void
Reactor_dealloc(ReactorObject *self)
{
	PyObject_GC_UnTrack(self);
	reactor_shutdown(self);
	Py_CLEAR(self->loop);
	Py_CLEAR(self->reap_cb);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(Reactor_attach__doc__,
"attach(loop)\n"
"--\n\n"
"Bind the reactor to an event loop and start reaping completions.\n\n"
"Registers the ring file descriptor with loop.add_reader(). Must be called\n"
"from the loop's own thread, which then becomes the only thread permitted to\n"
"submit.\n\n"
"Note that add_reader makes the loop hold a reference to this reactor, so\n"
"detach() or close() must be called for it to be collected.\n\n"
"Parameters\n"
"----------\n"
"loop : asyncio.AbstractEventLoop\n"
"    The loop to attach to.\n");

static PyObject *
Reactor_attach(ReactorObject *self, PyObject *loop)
{
	PyObject *reap = NULL;
	PyObject *ret = NULL;

	if (self->closed || !self->ring_ready) {
		PyErr_SetString(PyExc_ValueError, "Reactor is closed");
		return NULL;
	}
	if (self->attached) {
		PyErr_SetString(PyExc_RuntimeError,
				"Reactor is already attached to a loop");
		return NULL;
	}

	reap = PyObject_GetAttrString((PyObject *)self, "_reap");
	if (reap == NULL) {
		return NULL;
	}

	ret = PyObject_CallMethod(loop, "add_reader", "iO", self->ring_fd, reap);
	if (ret == NULL) {
		Py_DECREF(reap);
		return NULL;
	}
	Py_DECREF(ret);

	self->loop = Py_NewRef(loop);
	self->reap_cb = reap;	/* steals */
	self->attached = true;
	Py_RETURN_NONE;
}

PyDoc_STRVAR(Reactor_detach__doc__,
"detach()\n"
"--\n\n"
"Stop reaping completions and release the event loop. Idempotent.\n\n"
"In-flight operations are left alone; their Futures will never be settled\n"
"unless the reactor is attached again.\n");

static PyObject *
Reactor_detach(ReactorObject *self, PyObject *Py_UNUSED(ignored))
{
	if (reactor_detach(self) < 0) {
		return NULL;
	}
	Py_RETURN_NONE;
}

PyDoc_STRVAR(Reactor_close__doc__,
"close()\n"
"--\n\n"
"Detach, cancel every in-flight operation, drain the ring and release it.\n"
"Idempotent.\n\n"
"Buffers belonging to in-flight operations are held until their completions\n"
"reap. If the drain cannot complete, they are leaked rather than freed --\n"
"the kernel may still be writing into them.\n");

static PyObject *
Reactor_close(ReactorObject *self, PyObject *Py_UNUSED(ignored))
{
	reactor_shutdown(self);
	Py_RETURN_NONE;
}

PyDoc_STRVAR(Reactor_fileno__doc__,
"fileno()\n"
"--\n\n"
"Return the io_uring file descriptor.\n\n"
"Pollable: the kernel reports EPOLLIN whenever completions are pending, which\n"
"is what makes loop.add_reader() sufficient with no eventfd.\n");

static PyObject *
Reactor_fileno(ReactorObject *self, PyObject *Py_UNUSED(ignored))
{
	if (self->closed || !self->ring_ready) {
		PyErr_SetString(PyExc_ValueError, "Reactor is closed");
		return NULL;
	}
	return PyLong_FromLong(self->ring_fd);
}

PyDoc_STRVAR(Reactor_register_self__doc__,
"register_self()\n"
"--\n\n"
"Register this process's own credentials as a personality.\n\n"
"Registering your own credentials requires no privilege, so this is the\n"
"unprivileged path through the same per-SQE stamp machinery that brokered\n"
"cross-user identities use.\n\n"
"The snapshot is frozen at registration: later credential or group changes\n"
"are invisible to an already-registered personality.\n\n"
"Returns\n"
"-------\n"
"Personality\n"
"    A registered identity, always with a non-zero id.\n");

static PyObject *
Reactor_register_self(ReactorObject *self, PyObject *Py_UNUSED(ignored))
{
	PersonalityObject *pers = NULL;
	int ret = 0;

	if (self->closed || !self->ring_ready) {
		PyErr_SetString(PyExc_ValueError, "Reactor is closed");
		return NULL;
	}

	Py_BEGIN_ALLOW_THREADS
	ret = io_uring_register_personality(&self->ring);
	Py_END_ALLOW_THREADS

	if (ret < 0) {
		return uring_set_error(ret);
	}
	if (ret == 0) {
		/*
		 * The personalities xarray is XA_FLAGS_ALLOC1, so id 0 is
		 * never allocated and 0 unambiguously means "ambient creds".
		 * A 0 here would break that invariant.
		 */
		PyErr_SetString(PyExc_OSError,
				"kernel returned personality id 0, which is "
				"reserved for ambient credentials");
		return NULL;
	}

	pers = PyObject_GC_New(PersonalityObject, &PersonalityType);
	if (pers == NULL) {
		io_uring_unregister_personality(&self->ring, ret);
		return NULL;
	}
	pers->reactor = Py_NewRef((PyObject *)self);
	pers->id = (unsigned int)ret;
	PyObject_GC_Track(pers);
	return (PyObject *)pers;
}

static PyObject *
Reactor_get_inflight(ReactorObject *self, void *Py_UNUSED(closure))
{
	return PyLong_FromUnsignedLong(self->inflight);
}

static PyObject *
Reactor_get_closed(ReactorObject *self, void *Py_UNUSED(closure))
{
	return PyBool_FromLong(self->closed);
}

static PyObject *
Reactor_get_attached(ReactorObject *self, void *Py_UNUSED(closure))
{
	return PyBool_FromLong(self->attached);
}

static PyMethodDef Reactor_methods[] = {
	{"_reap", (PyCFunction)Reactor_reap, METH_NOARGS, Reactor_reap__doc__},
	{"attach", (PyCFunction)Reactor_attach, METH_O, Reactor_attach__doc__},
	{"detach", (PyCFunction)Reactor_detach, METH_NOARGS, Reactor_detach__doc__},
	{"close", (PyCFunction)Reactor_close, METH_NOARGS, Reactor_close__doc__},
	{"fileno", (PyCFunction)Reactor_fileno, METH_NOARGS, Reactor_fileno__doc__},
	{"register_self", (PyCFunction)Reactor_register_self, METH_NOARGS,
	 Reactor_register_self__doc__},

	/* operations -- defined in ops.c */
	{"open", (PyCFunction)(void (*)(void))Reactor_open,
	 METH_VARARGS | METH_KEYWORDS, Reactor_open__doc__},
	{"pread", (PyCFunction)(void (*)(void))Reactor_pread,
	 METH_VARARGS | METH_KEYWORDS, Reactor_pread__doc__},
	{"pwrite", (PyCFunction)(void (*)(void))Reactor_pwrite,
	 METH_VARARGS | METH_KEYWORDS, Reactor_pwrite__doc__},
	{"fsync", (PyCFunction)(void (*)(void))Reactor_fsync,
	 METH_VARARGS | METH_KEYWORDS, Reactor_fsync__doc__},
	{"statx", (PyCFunction)(void (*)(void))Reactor_statx,
	 METH_VARARGS | METH_KEYWORDS, Reactor_statx__doc__},
	{"close_file", (PyCFunction)Reactor_close_file, METH_O,
	 Reactor_close_file__doc__},
	{NULL},
};

static PyGetSetDef Reactor_getsetters[] = {
	{discard_const_p(char, "inflight"), (getter)Reactor_get_inflight, NULL,
	 discard_const_p(char, "Number of operations currently in flight"), NULL},
	{discard_const_p(char, "closed"), (getter)Reactor_get_closed, NULL,
	 discard_const_p(char, "True once close() has been called"), NULL},
	{discard_const_p(char, "attached"), (getter)Reactor_get_attached, NULL,
	 discard_const_p(char, "True while attached to an event loop"), NULL},
	{NULL},
};

PyTypeObject ReactorType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_os.uring.Reactor",
	.tp_basicsize = sizeof(ReactorObject),
	.tp_dealloc = (destructor)Reactor_dealloc,
	.tp_traverse = (traverseproc)Reactor_traverse,
	.tp_clear = (inquiry)Reactor_clear,
	.tp_methods = Reactor_methods,
	.tp_getset = Reactor_getsetters,
	.tp_new = Reactor_new,
	.tp_doc = reactor__doc__,
	.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
};

/* -- Personality ---------------------------------------------------------- */

PyDoc_STRVAR(personality__doc__,
"A registered io_uring personality: a frozen snapshot of credentials.\n\n"
"Captures uid, gid, euid, fsuid, fsgid, the full supplementary group list,\n"
"all five capability sets, the LSM label, keyrings and the user namespace.\n\n"
"It captures none of: cwd, root directory, umask, any namespace, rlimits or\n"
"the file descriptor table -- struct cred holds no fs_struct. A personality\n"
"decides *who* an operation runs as; an Anchor decides *where*.\n\n"
"Not constructible directly; use Reactor.register_self().\n");

static void
Personality_dealloc(PersonalityObject *self)
{
	PyObject_GC_UnTrack(self);

	if (self->id != 0 && self->reactor != NULL) {
		ReactorObject *r = (ReactorObject *)self->reactor;
		if (r->ring_ready && !r->closed) {
			io_uring_unregister_personality(&r->ring, self->id);
		}
		self->id = 0;
	}

	Py_CLEAR(self->reactor);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

static int
Personality_traverse(PersonalityObject *self, visitproc visit, void *arg)
{
	Py_VISIT(self->reactor);
	return 0;
}

static int
Personality_clear(PersonalityObject *self)
{
	Py_CLEAR(self->reactor);
	return 0;
}

static PyObject *
Personality_repr(PersonalityObject *self)
{
	if (self->id == 0) {
		return PyUnicode_FromString("<Personality (unregistered)>");
	}
	return PyUnicode_FromFormat("<Personality id=%u>", self->id);
}

PyDoc_STRVAR(Personality_unregister__doc__,
"unregister()\n"
"--\n\n"
"Release this personality's id. Idempotent.\n\n"
"Operations already in flight are unaffected: each request took its own\n"
"reference on the credentials at submission, so they complete under the\n"
"identity they were stamped with. Newly submitted operations naming a\n"
"released id fail with EINVAL.\n");

static PyObject *
Personality_unregister(PersonalityObject *self, PyObject *Py_UNUSED(ignored))
{
	ReactorObject *r = NULL;
	int ret = 0;

	if (self->id == 0) {
		Py_RETURN_NONE;
	}

	r = (ReactorObject *)self->reactor;
	if (r != NULL && r->ring_ready && !r->closed) {
		Py_BEGIN_ALLOW_THREADS
		ret = io_uring_unregister_personality(&r->ring, self->id);
		Py_END_ALLOW_THREADS

		if (ret < 0) {
			return uring_set_error(ret);
		}
	}

	self->id = 0;
	Py_RETURN_NONE;
}

static PyObject *
Personality_get_id(PersonalityObject *self, void *Py_UNUSED(closure))
{
	return PyLong_FromUnsignedLong(self->id);
}

static PyMethodDef Personality_methods[] = {
	{"unregister", (PyCFunction)Personality_unregister, METH_NOARGS,
	 Personality_unregister__doc__},
	{NULL},
};

static PyGetSetDef Personality_getsetters[] = {
	{discard_const_p(char, "id"), (getter)Personality_get_id, NULL,
	 discard_const_p(char, "Registered personality id (0 once unregistered)"), NULL},
	{NULL},
};

PyTypeObject PersonalityType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_os.uring.Personality",
	.tp_basicsize = sizeof(PersonalityObject),
	.tp_dealloc = (destructor)Personality_dealloc,
	.tp_traverse = (traverseproc)Personality_traverse,
	.tp_clear = (inquiry)Personality_clear,
	.tp_repr = (reprfunc)Personality_repr,
	.tp_methods = Personality_methods,
	.tp_getset = Personality_getsetters,
	.tp_doc = personality__doc__,
	.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
};

/* -- FixedFile ------------------------------------------------------------ */

PyDoc_STRVAR(fixedfile__doc__,
"An open file living in the reactor's registered file table.\n\n"
"There is no process file descriptor: the file was installed directly into a\n"
"registered slot by the open, so it is addressed by index and IOSQE_FIXED_FILE\n"
"rather than by fd.\n\n"
"Not constructible directly; returned by Reactor.open().\n");

static void
FixedFile_dealloc(FixedFileObject *self)
{
	PyObject_GC_UnTrack(self);
	Py_CLEAR(self->reactor);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

static int
FixedFile_traverse(FixedFileObject *self, visitproc visit, void *arg)
{
	Py_VISIT(self->reactor);
	return 0;
}

static int
FixedFile_clear(FixedFileObject *self)
{
	Py_CLEAR(self->reactor);
	return 0;
}

static PyObject *
FixedFile_repr(FixedFileObject *self)
{
	if (self->closed) {
		return PyUnicode_FromString("<FixedFile (closed)>");
	}
	return PyUnicode_FromFormat("<FixedFile slot=%u>", self->slot);
}

static PyObject *
FixedFile_get_slot(FixedFileObject *self, void *Py_UNUSED(closure))
{
	return PyLong_FromUnsignedLong(self->slot);
}

static PyObject *
FixedFile_get_closed(FixedFileObject *self, void *Py_UNUSED(closure))
{
	return PyBool_FromLong(self->closed);
}

static PyGetSetDef FixedFile_getsetters[] = {
	{discard_const_p(char, "slot"), (getter)FixedFile_get_slot, NULL,
	 discard_const_p(char, "Registered file table index"), NULL},
	{discard_const_p(char, "closed"), (getter)FixedFile_get_closed, NULL,
	 discard_const_p(char, "True once the file has been closed"), NULL},
	{NULL},
};

PyTypeObject FixedFileType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_os.uring.FixedFile",
	.tp_basicsize = sizeof(FixedFileObject),
	.tp_dealloc = (destructor)FixedFile_dealloc,
	.tp_traverse = (traverseproc)FixedFile_traverse,
	.tp_clear = (inquiry)FixedFile_clear,
	.tp_repr = (reprfunc)FixedFile_repr,
	.tp_getset = FixedFile_getsetters,
	.tp_doc = fixedfile__doc__,
	.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
};

/* -- registration --------------------------------------------------------- */

int
init_reactor_types(PyObject *module)
{
	if (init_interned_names() < 0) {
		return -1;
	}

	if (PyType_Ready(&ReactorType) < 0) {
		return -1;
	}
	if (PyType_Ready(&PersonalityType) < 0) {
		return -1;
	}
	if (PyType_Ready(&FixedFileType) < 0) {
		return -1;
	}

	if (PyModule_AddObjectRef(module, "Reactor", (PyObject *)&ReactorType) < 0) {
		return -1;
	}
	if (PyModule_AddObjectRef(module, "Personality",
				  (PyObject *)&PersonalityType) < 0) {
		return -1;
	}
	if (PyModule_AddObjectRef(module, "FixedFile",
				  (PyObject *)&FixedFileType) < 0) {
		return -1;
	}

	return 0;
}
