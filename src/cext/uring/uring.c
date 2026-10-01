// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * The Uring: a minimal async file ring over io_uring.
 *
 * Six operations -- open / close / preadv2 / pwritev2 / fsync / statx -- and no
 * event-loop policy. prep_*() fills a pre-allocated op slot and returns an opaque
 * UringOp handle; submit(handles) copies each slot's SQE into the submission
 * queue and fires one io_uring_submit; reap() drains completions into plain
 * (token, res, result) tuples. No Python object ever wraps kernel SQE/CQE
 * memory -- the conversion happens at the boundary.
 *
 * Op slots come from a pre-allocated pool; uring.h has the slot layout, the
 * token encoding, and the FREE/PREPPED/INFLIGHT state machine. Every op is
 * single-shot -- exactly one CQE per submission, no multishot, no slot reused
 * before its CQE reaps -- so free-on-reap can never double-free or use-after-
 * free. submit() may set IOSQE_IO_LINK to order a chain; each linked op is still
 * single-shot.
 *
 * Concurrent submitters, one reaper: multiple threads may prep/submit/cancel (a
 * PyMutex serializes the single-producer SQ; the GIL serializes the pool,
 * inflight, and the single-consumer CQ), while a single thread owns
 * reap(). The ring fd is directly pollable (io_uring_poll() reports EPOLLIN when
 * completions are pending), so no eventfd is registered. The reap path respects
 * two poll consequences: a level-triggered poll wants the CQ fully drained, and
 * poll does not flush the overflow list, so EPOLLIN with an empty CQ is normal.
 */

#include <Python.h>
#include "pyuring_common.h"
#include "uring.h"
#include "op.h"
#include "submitreap.h"		/* submit/reap mechanics + uring_get_sqe */
#include "truenas_os_state.h"	/* module state: the UringOp repr strings */

/* Cap on cached dead handles, mirroring CPython's float freelist bound. */
#define URING_HANDLE_FREELIST_MAX	256

static PyTypeObject UringOpType;

/* -- errors --------------------------------------------------------------- */

static PyObject *
uring_set_error(int err)
{
	/*
	 * CQE results are negative errno; PyErr_SetFromErrno reads the global
	 * errno, so set it rather than inventing a message.
	 */
	errno = err < 0 ? -err : err;
	return PyErr_SetFromErrno(PyExc_OSError);
}

/* -- op handle ------------------------------------------------------------ */

/*
 * The opaque value prep_* returns and submit() consumes: a strong UringObject
 * ref plus the pool-slot index. A minimal non-GC custom type -- the sole
 * handle->ring edge cannot cycle (a ring never refers back to a handle), so it
 * needs no traverse/clear. Dead handles are cached on a per-ring freelist at
 * refcount 0 (CPython float discipline) and resurrected by handle_new.
 */
struct UringOpObject {
	PyObject_HEAD
	UringObject *ring;		/* strong ref while live; unused while dead */
	UringOpObject *next_free;	/* freelist chain while dead */
	uint32_t slot;
	uint8_t tag;			/* URING_TAG_* -- immutable; drives repr */
};

PyObject *
handle_new(UringObject *self, uint32_t slot)
{
	UringOpObject *h = NULL;

	if (self->handle_free != NULL) {
		h = self->handle_free;
		self->handle_free = h->next_free;
		self->nr_handle_free--;
		/* Resurrect the refcount-0 block: refcount -> 1, type intact. */
		_Py_NewReference((PyObject *)h);
	} else {
		h = PyObject_New(UringOpObject, &UringOpType);
		if (h == NULL) {
			return NULL;
		}
	}

	h->next_free = NULL;
	h->slot = slot;
	h->tag = self->pool[slot].tag;	/* the op's tag, set by the worker */
	h->ring = self;
	Py_INCREF(self);
	return (PyObject *)h;
}

static void
ringop_dealloc(UringOpObject *self)
{
	UringObject *ring = self->ring;
	uint32_t slot = self->slot;

	/*
	 * A handle dropped while its slot is still PREPPED (never submitted)
	 * reclaims the slot, releasing its pin/path. Only while the ring is
	 * open -- once closed, uring_shutdown has already released every
	 * PREPPED pin (and may have freed the pool), and the strong ring ref
	 * below keeps the `closed` read valid.
	 */
	if (!ring->closed && ring->pool != NULL && slot != URING_NO_SLOT &&
	    ring->pool[slot].state == URING_OP_PREPPED) {
		pool_recycle(ring, slot);
	}

	if (!ring->closed && ring->nr_handle_free < URING_HANDLE_FREELIST_MAX) {
		/*
		 * Cache the dead block at refcount 0 (do not touch ob_type). The
		 * ring's own teardown frees the freelist, so pushing before the
		 * DECREF is safe even if this handle held the ring's last ref:
		 * the DECREF then runs uring_shutdown, which PyObject_Free's
		 * this block along with the rest of the list.
		 */
		self->next_free = ring->handle_free;
		ring->handle_free = self;
		ring->nr_handle_free++;
		Py_DECREF(ring);
		return;
	}

	Py_DECREF(ring);
	PyObject_Free(self);
}

/*
 * The per-op-type repr strings live in module state (built once in
 * init_uring_types). A handle's tag is immutable, so the repr never needs
 * formatting -- fetch the state and hand out a new ref to the cached string.
 */
static PyObject *
ringop_repr(UringOpObject *self)
{
	truenas_os_state_t *state = get_truenas_os_state(NULL);

	if (state == NULL) {
		PyErr_SetString(PyExc_RuntimeError,
				"truenas_os module state is unavailable");
		return NULL;
	}
	switch (self->tag) {
	case URING_TAG_OPEN:
		return Py_NewRef(state->uring_repr_openat2);
	case URING_TAG_READ:
		return Py_NewRef(state->uring_repr_preadv2);
	case URING_TAG_WRITE:
		return Py_NewRef(state->uring_repr_pwritev2);
	case URING_TAG_CLOSE:
		return Py_NewRef(state->uring_repr_close);
	case URING_TAG_STATX:
		return Py_NewRef(state->uring_repr_statx);
	case URING_TAG_FSYNC:
		return Py_NewRef(state->uring_repr_fsync);
	}
	Py_RETURN_NONE;	/* unreachable: tag is always a valid URING_TAG_* */
}

PyDoc_STRVAR(ringop__doc__,
"An opaque handle for one prepared io_uring operation.\n\n"
"Returned by Uring.prep_openat2/prep_close/prep_preadv2/prep_pwritev2/\n"
"prep_fsync/prep_statx and consumed by Uring.submit(). It is not constructible\n"
"from Python and exposes no members.\n"
"Dropping a prepared handle before it is submitted reclaims its slot and\n"
"releases any pinned buffers; a submitted handle is inert.\n");

static PyTypeObject UringOpType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_os.UringOp",
	.tp_basicsize = sizeof(UringOpObject),
	.tp_dealloc = (destructor)ringop_dealloc,
	.tp_repr = (reprfunc)ringop_repr,
	.tp_doc = ringop__doc__,
	/*
	 * No Py_TPFLAGS_HAVE_GC (the handle->ring edge cannot cycle) and no
	 * Py_TPFLAGS_BASETYPE (opaque, non-subclassable).
	 */
	.tp_flags = Py_TPFLAGS_DEFAULT,
};

/* -- prep_* method stubs -------------------------------------------------- */

/*
 * These are the METH_FASTCALL entry points named in the method table. Each does
 * the Python-level argument parsing/validation and then calls its worker in
 * openclose.c / rw.c / fsync.c / stat.c (declared in op.h), which owns the
 * op-slot mechanics. Every prep except the positional-only prep_close
 * additionally takes keywords via map_kwargs() with a kwnames == NULL fast
 * path.
 */

PyDoc_STRVAR(py_uring_ring_prep_openat2__doc__,
"prep_openat2($self, dirfd, path, flags=0, mode=0, resolve=RESOLVE_BENEATH, callback=None, private_data=None)\n"
"--\n\n"
"Prepare an openat2 that opens a regular process file descriptor. `path`\n"
"resolves against `dirfd` (a real O_PATH directory fd) confined by\n"
"RESOLVE_BENEATH. Returns an opaque handle for submit(); its completion result\n"
"is the new int fd, owned by the caller like any os.open result.\n");

/*
 * Normalize + validate the optional per-op callback/private_data (both borrowed).
 * A None callback means "no callback"; private_data without a callback is
 * rejected. Callers invoke this only when one was actually passed, so a
 * callback-free op pays nothing. On return *callback is NULL (absent) or a
 * callable. Returns 0, or -1 with an exception set.
 */
static int
prep_norm_cb(PyObject **callback, PyObject *private_data)
{
	PyObject *cb = *callback;

	if (cb == Py_None) {
		cb = NULL;
	}
	if (cb != NULL && !PyCallable_Check(cb)) {
		PyErr_SetString(PyExc_TypeError,
				"callback must be a callable or None");
		return -1;
	}
	if (private_data != NULL && cb == NULL) {
		PyErr_SetString(PyExc_TypeError,
				"private_data given without a callback");
		return -1;
	}
	*callback = cb;
	return 0;
}

/*
 * Collect the positional-or-keyword arguments of a METH_KEYWORDS prep into
 * slots[nparams] (borrowed; NULL where an optional argument was not supplied).
 * The first `nrequired` parameters are required; the rest are optional. One
 * shared collector enforces that shape (and one set of messages) for every
 * keyword-taking prep. The common kwnames == NULL call takes the fast
 * positional arm; only a keyword-bearing call runs map_kwargs. Returns 0, or -1
 * with an exception set.
 */
static int
prep_collect(const char *funcname, PyObject *const *args, Py_ssize_t nargs,
	     PyObject *kwnames, const char *const *params, Py_ssize_t nparams,
	     Py_ssize_t nrequired, PyObject **slots)
{
	Py_ssize_t i = 0;

	if (kwnames == NULL) {
		/* Fast positional path: no keyword bookkeeping. */
		if (nargs < nrequired || nargs > nparams) {
			PyErr_Format(PyExc_TypeError,
				     "%s() takes %zd to %zd positional arguments",
				     funcname, nrequired, nparams);
			return -1;
		}
		for (i = 0; i < nparams; i++) {
			slots[i] = i < nargs ? args[i] : NULL;
		}
		return 0;
	}

	for (i = 0; i < nparams; i++) {
		slots[i] = NULL;
	}
	if (map_kwargs(funcname, args, nargs, kwnames, params, nparams, slots) < 0) {
		return -1;
	}
	for (i = 0; i < nrequired; i++) {
		if (slots[i] == NULL) {
			PyErr_Format(PyExc_TypeError,
				     "%s() missing required argument '%s'",
				     funcname, params[i]);
			return -1;
		}
	}
	return 0;
}

static PyObject *
py_uring_ring_prep_openat2(UringObject *self, PyObject *const *args,
			   Py_ssize_t nargs, PyObject *kwnames)
{
	static const char *const params[] = {"dirfd", "path", "flags", "mode",
					     "resolve", "callback", "private_data"};
	PyObject *a_dirfd = NULL, *a_path = NULL, *a_flags = NULL;
	PyObject *a_mode = NULL, *a_resolve = NULL;
	PyObject *a_callback = NULL, *a_private_data = NULL;
	PyObject *slots[URING_NELEM(params)] = {0};
	PyObject *path_bytes = NULL;
	PyObject *result = NULL;
	int dirfd = 0;
	int flags = O_RDONLY;
	int mode = 0;
	unsigned long long resolve = RESOLVE_BENEATH;

	if (uring_check_ready(self) < 0) {
		return NULL;
	}

	if (prep_collect("prep_openat2", args, nargs, kwnames, params,
			 URING_NELEM(params), 2, slots) < 0) {
		return NULL;
	}
	a_dirfd = slots[0];
	a_path = slots[1];
	a_flags = slots[2];
	a_mode = slots[3];
	a_resolve = slots[4];
	a_callback = slots[5];
	a_private_data = slots[6];

	dirfd = PyLong_AsInt(a_dirfd);
	if (dirfd == -1 && PyErr_Occurred()) {
		return NULL;
	}
	if (a_flags != NULL) {
		flags = PyLong_AsInt(a_flags);
		if (flags == -1 && PyErr_Occurred()) {
			return NULL;
		}
	}
	if (a_mode != NULL) {
		mode = PyLong_AsInt(a_mode);
		if (mode == -1 && PyErr_Occurred()) {
			return NULL;
		}
	}
	if (a_resolve != NULL) {
		resolve = PyLong_AsUnsignedLongLong(a_resolve);
		if (resolve == (unsigned long long)-1 && PyErr_Occurred()) {
			return NULL;
		}
	}

	if ((a_callback != NULL || a_private_data != NULL) &&
	    prep_norm_cb(&a_callback, a_private_data) < 0) {
		return NULL;
	}

	/* str/bytes/PathLike; embedded NUL rejected. The worker takes its own
	 * ref to the produced bytes, so ours drops right after the call. */
	if (!PyUnicode_FSConverter(a_path, &path_bytes)) {
		return NULL;
	}

	result = uring_op_openat2(self, dirfd, path_bytes, flags, mode,
				  resolve, a_callback, a_private_data);
	Py_DECREF(path_bytes);
	return result;
}

PyDoc_STRVAR(py_uring_ring_prep_close__doc__,
"prep_close($self, fd, callback=None, private_data=None, /)\n"
"--\n\n"
"Prepare a close(2) of the file descriptor `fd`. Nothing orders it against\n"
"other operations on the same fd -- as with os.close, submit the close after\n"
"their completions reap, or behind them in a linked batch. Returns an opaque\n"
"handle for submit(); its completion result is None.\n");

static PyObject *
py_uring_ring_prep_close(UringObject *self, PyObject *const *args,
			 Py_ssize_t nargs)
{
	PyObject *callback = NULL, *private_data = NULL;
	int fd = -1;

	if (uring_check_ready(self) < 0) {
		return NULL;
	}
	if (nargs < 1 || nargs > 3) {
		PyErr_SetString(PyExc_TypeError,
				"prep_close() takes 1 to 3 arguments");
		return NULL;
	}

	fd = PyLong_AsInt(args[0]);
	if (fd == -1 && PyErr_Occurred()) {
		return NULL;
	}

	callback = nargs >= 2 ? args[1] : NULL;
	private_data = nargs >= 3 ? args[2] : NULL;
	if ((callback != NULL || private_data != NULL) &&
	    prep_norm_cb(&callback, private_data) < 0) {
		return NULL;
	}

	return uring_op_close(self, fd, callback, private_data);
}

/* Shared parsing for prep_preadv2 / prep_pwritev2; the worker is uring_op_rw(). */
static PyObject *
prep_rwv_stub(UringObject *self, PyObject *const *args, Py_ssize_t nargs,
	      PyObject *kwnames, bool is_write)
{
	static const char *const params[] = {"fd", "buffers", "offset",
					     "flags", "callback", "private_data"};
	PyObject *a_fd = NULL, *a_buffers = NULL, *a_offset = NULL;
	PyObject *a_flags = NULL;
	PyObject *a_callback = NULL, *a_private_data = NULL;
	PyObject *slots[URING_NELEM(params)] = {0};
	unsigned long long offset = 0;
	int fd = -1;
	int flags = 0;

	if (uring_check_ready(self) < 0) {
		return NULL;
	}

	if (prep_collect(is_write ? "prep_pwritev2" : "prep_preadv2", args,
			 nargs, kwnames, params, URING_NELEM(params), 2,
			 slots) < 0) {
		return NULL;
	}
	a_fd = slots[0];
	a_buffers = slots[1];
	a_offset = slots[2];
	a_flags = slots[3];
	a_callback = slots[4];
	a_private_data = slots[5];

	fd = PyLong_AsInt(a_fd);
	if (fd == -1 && PyErr_Occurred()) {
		return NULL;
	}
	if (a_offset != NULL) {
		offset = PyLong_AsUnsignedLongLong(a_offset);
		if (offset == (unsigned long long)-1 && PyErr_Occurred()) {
			return NULL;
		}
	}
	if (a_flags != NULL) {
		flags = PyLong_AsInt(a_flags);
		if (flags == -1 && PyErr_Occurred()) {
			return NULL;
		}
	}

	if ((a_callback != NULL || a_private_data != NULL) &&
	    prep_norm_cb(&a_callback, a_private_data) < 0) {
		return NULL;
	}

	return uring_op_rw(self, fd, a_buffers, offset, flags, is_write,
			   a_callback, a_private_data);
}

PyDoc_STRVAR(py_uring_ring_prep_preadv2__doc__,
"prep_preadv2($self, fd, buffers, offset=0, flags=0, callback=None, private_data=None)\n"
"--\n\n"
"Prepare a positional vectored read (preadv2(2)) from the file descriptor `fd`\n"
"into a sequence of writable buffers -- at most 8 per operation; split larger\n"
"transfers across operations. Every buffer is pinned from submission until\n"
"the completion reaps. `flags` takes per-IO\n"
"RWF_* values (os.RWF_HIPRI, os.RWF_NOWAIT, ...); the kernel validates them at\n"
"issue, so an unsupported flag surfaces as the completion's -errno. Returns an\n"
"opaque handle for submit(); its completion result is the int total byte\n"
"count. A short count is ordinary readv semantics.\n");

static PyObject *
py_uring_ring_prep_preadv2(UringObject *self, PyObject *const *args,
			   Py_ssize_t nargs, PyObject *kwnames)
{
	return prep_rwv_stub(self, args, nargs, kwnames, false);
}

PyDoc_STRVAR(py_uring_ring_prep_pwritev2__doc__,
"prep_pwritev2($self, fd, buffers, offset=0, flags=0, callback=None, private_data=None)\n"
"--\n\n"
"Prepare a positional vectored write (pwritev2(2)) of a sequence of buffers --\n"
"at most 8 per operation; split larger transfers across operations -- to the\n"
"file descriptor `fd`. Every buffer is pinned from submission until the\n"
"completion reaps. `flags` takes per-IO RWF_*\n"
"values (os.RWF_DSYNC, os.RWF_APPEND, ...); the kernel validates them at\n"
"issue, so an unsupported flag surfaces as the completion's -errno. Returns an\n"
"opaque handle for submit(); its completion result is the int total byte\n"
"count. A short count is ordinary writev semantics.\n");

static PyObject *
py_uring_ring_prep_pwritev2(UringObject *self, PyObject *const *args,
			    Py_ssize_t nargs, PyObject *kwnames)
{
	return prep_rwv_stub(self, args, nargs, kwnames, true);
}

PyDoc_STRVAR(py_uring_ring_prep_fsync__doc__,
"prep_fsync($self, fd, fdatasync=False, offset=0, length=0, callback=None, private_data=None)\n"
"--\n\n"
"Prepare an fsync(2) of the file descriptor `fd` -- fdatasync(2) semantics\n"
"when fdatasync=True. With the default offset=0, length=0 the whole file is\n"
"synced; a nonzero length limits the sync to the byte range\n"
"[offset, offset+length]. There is no offset-to-EOF form: a nonzero offset\n"
"with length=0 degenerates to a one-byte range. Like os.fsync, nothing orders\n"
"it against other operations on the same fd -- submit it after their\n"
"completions reap, or behind them in a linked batch. Always runs on an io-wq\n"
"worker (the kernel forces fsync async). Returns an opaque handle for\n"
"submit(); its completion result is None.\n");

static PyObject *
py_uring_ring_prep_fsync(UringObject *self, PyObject *const *args,
			 Py_ssize_t nargs, PyObject *kwnames)
{
	static const char *const params[] = {"fd", "fdatasync", "offset",
					     "length", "callback", "private_data"};
	PyObject *a_fd = NULL, *a_fdatasync = NULL, *a_offset = NULL;
	PyObject *a_length = NULL;
	PyObject *a_callback = NULL, *a_private_data = NULL;
	PyObject *slots[URING_NELEM(params)] = {0};
	unsigned long long offset = 0;
	unsigned long long length_arg = 0;
	int fd = -1;
	int datasync = 0;

	if (uring_check_ready(self) < 0) {
		return NULL;
	}

	if (prep_collect("prep_fsync", args, nargs, kwnames, params,
			 URING_NELEM(params), 1, slots) < 0) {
		return NULL;
	}
	a_fd = slots[0];
	a_fdatasync = slots[1];
	a_offset = slots[2];
	a_length = slots[3];
	a_callback = slots[4];
	a_private_data = slots[5];

	fd = PyLong_AsInt(a_fd);
	if (fd == -1 && PyErr_Occurred()) {
		return NULL;
	}
	if (a_fdatasync != NULL) {
		datasync = PyObject_IsTrue(a_fdatasync);
		if (datasync < 0) {
			return NULL;
		}
	}
	if (a_offset != NULL) {
		offset = PyLong_AsUnsignedLongLong(a_offset);
		if (offset == (unsigned long long)-1 && PyErr_Occurred()) {
			return NULL;
		}
	}
	if (a_length != NULL) {
		length_arg = PyLong_AsUnsignedLongLong(a_length);
		if (length_arg == (unsigned long long)-1 && PyErr_Occurred()) {
			return NULL;
		}
		/*
		 * io_uring's fsync length is the SQE's 32-bit len field. A larger
		 * value would be silently truncated to its low 32 bits, syncing
		 * the wrong range with no error -- reject it.
		 */
		if (length_arg > (unsigned long long)UINT_MAX) {
			PyErr_Format(PyExc_ValueError,
				     "length too large for a single operation: "
				     "%llu (max %u)", length_arg, UINT_MAX);
			return NULL;
		}
	}

	if ((a_callback != NULL || a_private_data != NULL) &&
	    prep_norm_cb(&a_callback, a_private_data) < 0) {
		return NULL;
	}

	return uring_op_fsync(self, fd, datasync, offset,
			      (unsigned int)length_arg, a_callback,
			      a_private_data);
}

PyDoc_STRVAR(py_uring_ring_prep_statx__doc__,
"prep_statx($self, dirfd, path, flags=0, mask=STATX_BASIC_STATS|STATX_BTIME, callback=None, private_data=None)\n"
"--\n\n"
"Prepare a statx of `path` relative to `dirfd` (a real fd). Pass AT_EMPTY_PATH\n"
"with an empty path to statx `dirfd` itself. Unlike prep_openat2 there is no\n"
"RESOLVE_BENEATH confinement -- statx(2) has none -- so `path` resolves with\n"
"ordinary AT_* semantics. Returns an opaque handle for submit(); its completion\n"
"result is a truenas_os StatxResult.\n");

static PyObject *
py_uring_ring_prep_statx(UringObject *self, PyObject *const *args,
			 Py_ssize_t nargs, PyObject *kwnames)
{
	static const char *const params[] = {"dirfd", "path", "flags", "mask",
					     "callback", "private_data"};
	PyObject *a_dirfd = NULL, *a_path = NULL, *a_flags = NULL, *a_mask = NULL;
	PyObject *a_callback = NULL, *a_private_data = NULL;
	PyObject *slots[URING_NELEM(params)] = {0};
	PyObject *path_bytes = NULL;
	PyObject *result = NULL;
	unsigned long mask_arg = 0;
	int dirfd = 0;
	int flags = 0;
	unsigned int mask = STATX_BASIC_STATS | STATX_BTIME;

	if (uring_check_ready(self) < 0) {
		return NULL;
	}

	if (prep_collect("prep_statx", args, nargs, kwnames, params,
			 URING_NELEM(params), 2, slots) < 0) {
		return NULL;
	}
	a_dirfd = slots[0];
	a_path = slots[1];
	a_flags = slots[2];
	a_mask = slots[3];
	a_callback = slots[4];
	a_private_data = slots[5];

	dirfd = PyLong_AsInt(a_dirfd);
	if (dirfd == -1 && PyErr_Occurred()) {
		return NULL;
	}
	if (a_flags != NULL) {
		flags = PyLong_AsInt(a_flags);
		if (flags == -1 && PyErr_Occurred()) {
			return NULL;
		}
	}
	if (a_mask != NULL) {
		mask_arg = PyLong_AsUnsignedLong(a_mask);
		if (mask_arg == (unsigned long)-1 && PyErr_Occurred()) {
			return NULL;
		}
		mask = (unsigned int)mask_arg;
	}

	if ((a_callback != NULL || a_private_data != NULL) &&
	    prep_norm_cb(&a_callback, a_private_data) < 0) {
		return NULL;
	}

	/* str/bytes/PathLike; embedded NUL rejected. The worker takes its own
	 * ref to the produced bytes, so ours drops right after the call. */
	if (!PyUnicode_FSConverter(a_path, &path_bytes)) {
		return NULL;
	}

	result = uring_op_statx(self, dirfd, path_bytes, flags, mask,
				a_callback, a_private_data);
	Py_DECREF(path_bytes);
	return result;
}

/* -- submit --------------------------------------------------------------- */

PyDoc_STRVAR(py_uring_ring_submit__doc__,
"submit($self, handles, /, linked=False)\n"
"--\n\n"
"Copy each prepared operation's SQE into the submission queue and fire one\n"
"io_uring_submit.\n\n"
"Thread-safe: multiple threads may submit (and prep/cancel) concurrently; a\n"
"single thread owns reap().\n\n"
"`handles` is an iterable of UringOp handles from prep_*(). Every handle is\n"
"validated (a UringOp of this ring, still prepared) and staged before anything\n"
"is submitted; if any is not, or the batch exceeds the ring's SQ depth, the\n"
"whole batch is rolled back -- nothing reaches the kernel and the handles stay\n"
"reusable -- and the error is raised. An empty iterable submits nothing.\n\n"
"With linked=True every handle but the last is chained with IOSQE_IO_LINK, so\n"
"the kernel runs them in order and cancels the rest of the chain on a failure.\n\n"
"Returns\n"
"-------\n"
"tuple[int, ...]\n"
"    The token for each operation, in order. Each identifies its\n"
"    completion when it is reaped, and is what cancel() takes. A submitted\n"
"    handle is inert; dropping it reclaims nothing.\n");

/*
 * Parse submit()'s optional `linked` flag (positional args[1], or the `linked`
 * keyword). The handles iterable is args[0] and is handled by the caller.
 * Returns 0, or -1 with an exception set.
 */
static int
submit_parse_linked(PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames,
		    int *linked)
{
	Py_ssize_t nkw = 0;
	Py_ssize_t i = 0;

	if (nargs < 1 || nargs > 2) {
		PyErr_SetString(PyExc_TypeError,
				"submit() takes 1 or 2 positional arguments");
		return -1;
	}
	if (nargs == 2) {
		*linked = PyObject_IsTrue(args[1]);
		if (*linked < 0) {
			return -1;
		}
	}
	if (kwnames == NULL) {
		return 0;
	}
	nkw = PyTuple_GET_SIZE(kwnames);
	for (i = 0; i < nkw; i++) {
		PyObject *name = PyTuple_GET_ITEM(kwnames, i);	/* borrowed */

		if (PyUnicode_CompareWithASCIIString(name, "linked") != 0) {
			PyErr_Format(PyExc_TypeError,
				     "submit() got an unexpected keyword argument "
				     "'%U'", name);
			return -1;
		}
		if (nargs == 2) {
			PyErr_SetString(PyExc_TypeError,
					"submit() got multiple values for argument "
					"'linked'");
			return -1;
		}
		*linked = PyObject_IsTrue(args[nargs + i]);
		if (*linked < 0) {
			return -1;
		}
	}
	return 0;
}

/*
 * Validate each handle and record its slot -- the Python-object half of submit,
 * done under the GIL with no lock. Checks type, ring ownership, and that the
 * handle still carries a slot (a submitted or reclaimed handle has NO_SLOT, and
 * indexing the pool with NO_SLOT would be a wild access). The PREPPED / no-
 * duplicate check is a ring-mechanics concern deferred to uring_submit_batch,
 * which holds the lock and flips PREPPED -> INFLIGHT as it stages. Returns 0, or
 * -1 with an exception set.
 */
static int
submit_collect(UringObject *self, PyObject *fast, Py_ssize_t n,
	       submit_ent_t *ents)
{
	Py_ssize_t i = 0;

	for (i = 0; i < n; i++) {
		PyObject *item = PySequence_Fast_GET_ITEM(fast, i);	/* borrowed */
		UringOpObject *h = NULL;

		if (!PyObject_TypeCheck(item, &UringOpType)) {
			PyErr_Format(PyExc_TypeError,
				     "submit() item %zd is not a UringOp handle, "
				     "got %.200s", i, Py_TYPE(item)->tp_name);
			return -1;
		}
		h = (UringOpObject *)item;
		if (h->ring != self) {
			PyErr_Format(PyExc_ValueError,
				     "submit() item %zd is a handle from a "
				     "different Uring", i);
			return -1;
		}
		if (h->slot == URING_NO_SLOT) {
			PyErr_Format(PyExc_ValueError,
				     "submit() item %zd is not a prepared handle "
				     "(already submitted, or reclaimed)", i);
			return -1;
		}
		ents[i].slot = h->slot;
	}
	return 0;
}

static PyObject *
py_uring_ring_submit(UringObject *self, PyObject *const *args, Py_ssize_t nargs,
		     PyObject *kwnames)
{
	submit_ent_t small_ents[URING_SUBMIT_STACK];
	submit_ent_t *ents = small_ents;
	PyObject *fast = NULL;
	PyObject *result = NULL;
	Py_ssize_t n = 0;
	Py_ssize_t i = 0;
	int linked = 0;

	if (uring_check_ready(self) < 0) {
		return NULL;
	}
	if (submit_parse_linked(args, nargs, kwnames, &linked) < 0) {
		return NULL;
	}

	fast = PySequence_Fast(args[0], "submit() argument must be iterable");
	if (fast == NULL) {
		return NULL;
	}
	n = PySequence_Fast_GET_SIZE(fast);
	if (n == 0) {
		Py_DECREF(fast);
		return PyTuple_New(0);
	}

	/* The common small batch stages on-stack; only a large one hits the heap. */
	if ((size_t)n > URING_SUBMIT_STACK) {
		ents = PyMem_RawMalloc((size_t)n * sizeof(submit_ent_t));
		if (ents == NULL) {
			Py_DECREF(fast);
			return PyErr_NoMemory();
		}
	}

	/*
	 * Validate (GIL) -> stage + submit (lock) -> consume handles + build the
	 * token tuple (GIL). Each phase runs only if the previous one succeeded; the
	 * single cleanup below frees `fast` and any heap `ents` on every path, so
	 * there is no unwind label.
	 */
	if (submit_collect(self, fast, n, ents) == 0 &&
	    uring_submit_batch(self, ents, n, linked) == 0) {
		/*
		 * The ops are in flight: consume every handle so it forgets its slot --
		 * dropping it then reclaims nothing and re-submitting it is refused, even
		 * after the slot is reaped and reused, closing an ABA where a stale
		 * handle's destructor would reclaim a later op's slot. Every item was
		 * validated as a UringOp above, so the cast is safe. Consume even on the
		 * signal path (ops committed); only the returned tokens are lost there.
		 */
		for (i = 0; i < n; i++) {
			((UringOpObject *)PySequence_Fast_GET_ITEM(fast, i))->slot =
				URING_NO_SLOT;
		}
		if (!PyErr_Occurred()) {
			result = PyTuple_New(n);
			if (result != NULL) {
				for (i = 0; i < n; i++) {
					PyObject *tok = PyLong_FromUnsignedLongLong(
						ents[i].token);

					if (tok == NULL) {
						Py_CLEAR(result);
						break;
					}
					PyTuple_SET_ITEM(result, i, tok);
				}
			}
		}
	}

	Py_DECREF(fast);
	if (ents != small_ents) {
		PyMem_RawFree(ents);
	}
	return result;
}

/* -- completion ----------------------------------------------------------- */

PyDoc_STRVAR(py_uring_ring_reap__doc__,
"reap($self, max=0, /)\n"
"--\n\n"
"Drain the completion queue and return the completions as a list.\n\n"
"Each element is a plain (token, res, result) tuple. res is the raw kernel\n"
"result: bytes transferred / a new fd on success, -errno on failure. result\n"
"is the per-op object built in C on success (an int fd for open, an int byte\n"
"count for read/write, a StatxResult for statx, None for close/fsync), None\n"
"on failure, or a captured exception when the result could not be built.\n\n"
"An op prepared with a callback is instead consumed: its callback is invoked\n"
"here with the completion tuple (and its private_data, if any), and the op is\n"
"not included in the returned list. A callback that raises is reported\n"
"unraisably and never aborts the drain. A callback must not call reap() or\n"
"close() on this ring (both raise RuntimeError inside a callback); do that after\n"
"reap() returns.\n\n"
"With max == 0 the whole queue is drained; a positive max caps the completions\n"
"processed (a consumed op counts even though it is not returned). The ring fd\n"
"is level-triggered, so a full drain is what an add_reader-style poller wants;\n"
"io_uring_poll() does not flush the overflow list, so a wakeup with zero\n"
"completions is normal. A per-completion allocation failure is reported\n"
"unraisably rather than raised, so a partial drain never wedges the queue.\n\n"
"Returns\n"
"-------\n"
"list[tuple[int, int, object]]\n");

static PyObject *
py_uring_ring_reap(UringObject *self, PyObject *args)
{
	struct io_uring_cqe *cqe = NULL;
	PyObject *list = NULL;
	Py_ssize_t max = 0;
	unsigned long count = 0;

	/*
	 * `n` (Py_ssize_t) rejects a negative or out-of-range cap with an exception;
	 * 0 means "drain the whole queue".
	 */
	if (!PyArg_ParseTuple(args, "|n:reap", &max)) {
		return NULL;
	}
	if (max < 0) {
		PyErr_SetString(PyExc_ValueError, "max must be non-negative");
		return NULL;
	}

	/*
	 * A completion callback runs arbitrary Python; calling reap() (or close())
	 * from inside one would drain/free the ring re-entrantly. Forbid it.
	 */
	if (self->reaping) {
		PyErr_SetString(PyExc_RuntimeError,
				"reap() is not re-entrant; do not call it from a "
				"completion callback");
		return NULL;
	}

	list = PyList_New(0);
	if (list == NULL) {
		return NULL;
	}

	if (self->closed || !self->ring_ready) {
		return list;
	}

	self->reaping = true;
	while (!self->closed && io_uring_peek_cqe(&self->ring, &cqe) == 0) {
		uint64_t ud = io_uring_cqe_get_data64(cqe);
		int res = cqe->res;
		PyObject *tuple = NULL;
		bool consumed = false;

		io_uring_cqe_seen(&self->ring, cqe);

		if (ud == URING_CANCEL_SENTINEL) {
			/*
			 * A cancel SQE's own completion (the sentinel is never a
			 * valid slot token -- slots encode as index + 1). Its
			 * result is advisory; nothing waits on it.
			 */
			continue;
		}

		if (self->inflight > 0) {
			self->inflight--;
		}

		tuple = reap_one(self, &self->pool[OPID_TO_SLOT_IDX(ud)], res,
				 &consumed);
		if (tuple == NULL) {
			/*
			 * Building the tuple failed. The drain must not raise
			 * mid-flight (a level-triggered poller would spin), so
			 * report and keep draining.
			 */
			PyErr_WriteUnraisable((PyObject *)self);
			continue;
		}
		/*
		 * A callback consumed the op: reap_one handed the completion to it,
		 * so it is not appended to the returned list. Otherwise append it.
		 * Either way we drop our own ref afterward (PyList_Append incref's).
		 */
		if (!consumed && PyList_Append(list, tuple) < 0) {
			Py_DECREF(tuple);
			PyErr_WriteUnraisable((PyObject *)self);
			continue;
		}
		Py_DECREF(tuple);

		count++;	/* a processed completion, consumed or returned */
		if (max > 0 && count >= (unsigned long)max) {
			break;
		}
	}

	self->reaping = false;
	return list;
}

PyDoc_STRVAR(py_uring_ring_cancel__doc__,
"cancel($self, token, /)\n"
"--\n\n"
"Ask the kernel to cancel the in-flight operation with this token.\n\n"
"Fire-and-forget: the cancellation's own completion is advisory and is dropped\n"
"by reap(). The targeted operation still delivers a completion (a cancelled op\n"
"typically reports ECANCELED), and its buffers stay pinned until that\n"
"completion reaps.\n\n"
"Parameters\n"
"----------\n"
"token : int\n"
"    A token previously returned by submit().\n");

static PyObject *
py_uring_ring_cancel(UringObject *self, PyObject *arg)
{
	struct io_uring_sqe *sqe = NULL;
	unsigned long long ud = 0;
	int ret = 0;

	if (uring_check_ready(self) < 0) {
		return NULL;
	}

	ud = PyLong_AsUnsignedLongLong(arg);
	if (ud == (unsigned long long)-1 && PyErr_Occurred()) {
		return NULL;
	}
	if (!OPID_VALID(self, ud)) {
		PyErr_Format(PyExc_ValueError,
			     "invalid op id: %llu is not a token submit() "
			     "returned for this ring", ud);
		return NULL;
	}

	/*
	 * cancel produces an SQE, so it takes the submit lock like submit() (and
	 * re-checks closed under it -- a concurrent close() may have fenced the ring).
	 */
	PyMutex_Lock(&self->submit_lock);
	if (self->closed) {
		PyMutex_Unlock(&self->submit_lock);
		PyErr_SetString(PyExc_ValueError, "Uring is closed");
		return NULL;
	}

	sqe = uring_get_sqe(self);
	if (sqe == NULL) {
		PyMutex_Unlock(&self->submit_lock);
		return NULL;
	}

	io_uring_prep_cancel64(sqe, ud, 0);
	/* The cancel SQE's own completion is dropped by reap() on sight. */
	io_uring_sqe_set_data64(sqe, URING_CANCEL_SENTINEL);

	Py_BEGIN_ALLOW_THREADS
	ret = io_uring_submit(&self->ring);
	Py_END_ALLOW_THREADS

	PyMutex_Unlock(&self->submit_lock);

	if (ret < 0) {
		return uring_set_error(ret);
	}
	Py_RETURN_NONE;
}

/* -- teardown ------------------------------------------------------------- */

/*
 * Ask the kernel to cancel every in-flight op, then wait until every CQE has
 * reaped, releasing each slot's pinned payloads from its own CQE.
 *
 * Returns 0 when the ring is fully drained. On failure returns -1, and the
 * caller must LEAK the pool rather than free it: the kernel may still hold
 * pointers into the buffers un-reaped slots own.
 */
static int
uring_drain(UringObject *self)
{
	struct io_uring_sqe *sqe = NULL;
	struct io_uring_cqe *cqe = NULL;
	int ret = 0;

	if (!self->ring_ready || self->inflight == 0) {
		return 0;
	}

	sqe = io_uring_get_sqe(&self->ring);
	if (sqe != NULL) {
		/* CANCEL_ANY matches every op; the target user_data (0) is unused. */
		io_uring_prep_cancel64(sqe, 0, IORING_ASYNC_CANCEL_ANY);
		io_uring_sqe_set_data64(sqe, URING_CANCEL_SENTINEL);
		Py_BEGIN_ALLOW_THREADS
		io_uring_submit(&self->ring);
		Py_END_ALLOW_THREADS
	}

	while (self->inflight > 0) {
		uint64_t ud = 0;
		int async_err = 0;

		/*
		 * Retry a bare -EINTR (a signal with no Python handler, or one whose
		 * handler did not raise): the CQE is still pending, so an interrupted
		 * wait must not abort the drain and leak the whole pool. Only a handler
		 * that actually raised (async_err) stops the loop.
		 */
		do {
			Py_BEGIN_ALLOW_THREADS
			ret = io_uring_wait_cqe(&self->ring, &cqe);
			Py_END_ALLOW_THREADS
		} while (ret == -EINTR && !(async_err = PyErr_CheckSignals()));

		if (async_err || ret < 0) {
			/*
			 * A signal handler raised, or the wait failed for real: return
			 * failure so uring_shutdown LEAKS the pool rather than freeing
			 * buffers the kernel may still write into.
			 */
			return -1;
		}

		ud = io_uring_cqe_get_data64(cqe);
		io_uring_cqe_seen(&self->ring, cqe);

		if (ud == URING_CANCEL_SENTINEL) {
			continue;	/* a cancel SQE's own advisory completion */
		}
		self->inflight--;
		/*
		 * GIL held here (only wait_cqe drops it), so releasing the
		 * slot's pinned Py_buffers is safe.
		 */
		{
			uring_op_t *op = &self->pool[OPID_TO_SLOT_IDX(ud)];
			op_free_payloads(op);
			op->state = URING_OP_FREE;
		}
	}

	return 0;
}

static void
uring_shutdown(UringObject *self)
{
	UringOpObject *h = NULL;
	uint32_t i = 0;
	bool drained = false;

	if (self->closed) {
		return;
	}
	self->closed = true;

	/*
	 * Release the pins of prepared-but-unsubmitted slots. The kernel never
	 * saw these SQEs, so their buffers can be released immediately.
	 */
	if (self->pool != NULL) {
		/* Only slots below the high-water mark were ever handed out. */
		for (i = 0; i < self->pool_hi; i++) {
			if (self->pool[i].state == URING_OP_PREPPED) {
				op_free_payloads(&self->pool[i]);
				self->pool[i].state = URING_OP_FREE;
			}
		}
	}

	drained = (uring_drain(self) == 0);

	if (self->ring_ready) {
		io_uring_queue_exit(&self->ring);
		self->ring_ready = false;
		self->ring_fd = -1;
	}

	if (drained) {
		PyMem_RawFree(self->pool);
	}
	/*
	 * else: the pool is deliberately leaked -- the drain failed, so the
	 * kernel may still be writing into buffers un-reaped slots own; freeing
	 * anything now risks a use-after-free in the kernel's hands.
	 */
	self->pool = NULL;
	self->nr_pool = 0;

	/*
	 * Free the dead-handle freelist blocks. No live handle references this
	 * ring at this point (a live handle holds a strong ring ref, so the ring
	 * could not be here), and no new handle can be resurrected onto the list
	 * because prep_* now fails the closed check.
	 */
	while (self->handle_free != NULL) {
		h = self->handle_free;
		self->handle_free = h->next_free;
		PyObject_Free(h);
	}
	self->nr_handle_free = 0;
}

/* -- Uring type ------------------------------------------------------------ */

/*
 * PyArg O& converter for a u32 ring-size argument: reject a negative or
 * out-of-range value with an exception (a value in [0, UINT_MAX] passes). An
 * omitted argument leaves *addr at its default -- O& is not called then.
 */
static int
conv_u32(PyObject *obj, void *addr)
{
	long long v = 0;

	if (!PyLong_Check(obj)) {
		PyErr_Format(PyExc_TypeError,
			     "integer argument expected, not %.200s",
			     Py_TYPE(obj)->tp_name);
		return 0;
	}
	v = PyLong_AsLongLong(obj);
	if (v == -1 && PyErr_Occurred()) {
		return 0;	/* OverflowError: does not fit in long long */
	}
	if (v < 0 || v > (long long)UINT_MAX) {
		PyErr_Format(PyExc_ValueError, "value out of range (0..%u)",
			     UINT_MAX);
		return 0;
	}
	*(unsigned int *)addr = (unsigned int)v;
	return 1;
}

PyDoc_STRVAR(uring__doc__,
"Uring(*, entries=256, cq_entries=0, iowq_max_bounded=0, iowq_max_unbounded=0)\n"
"--\n\n"
"A minimal async file ring over io_uring: prep_openat2/prep_close/prep_preadv2/\n"
"prep_pwritev2/prep_fsync/prep_statx return opaque handles, submit() fires a\n"
"batch, reap() drains the completions. No event-loop policy is baked in.\n\n"
"Multiple threads may prep/submit/cancel concurrently; a single thread reaps.\n"
"The ring fd (ringfd()) is pollable, so a higher layer can drive it from an\n"
"event loop.\n\n"
"Parameters\n"
"----------\n"
"entries : int\n"
"    Submission queue depth (rounded up to a power of two by the kernel) and\n"
"    the size of the op-slot pool -- the most operations that can be prepared\n"
"    or in flight at once.\n"
"cq_entries : int\n"
"    Completion queue depth. 0 selects the kernel default (2 * entries). The\n"
"    completion queue cannot be resized later, so size it up front -- 2-4x\n"
"    entries -- for a submission-heavy workload.\n"
"iowq_max_bounded : int\n"
"    Cap on io-wq workers serving bounded (regular-file) work; 0 leaves the\n"
"    kernel default. Every force-async operation (open-create, fsync, ...) runs\n"
"    on an io-wq worker, so a cap bounds the worker pool on a busy daemon.\n"
"iowq_max_unbounded : int\n"
"    Cap on io-wq workers serving unbounded work; 0 leaves the default.\n\n"
"Raises\n"
"------\n"
"OSError\n"
"    If io_uring is unavailable, disabled (kernel.io_uring_disabled) or blocked\n"
"    by seccomp.\n");

static PyObject *
py_uring_ring_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
	static char *kwlist[] = {"entries", "cq_entries",
				 "iowq_max_bounded", "iowq_max_unbounded", NULL};
	UringObject *self = NULL;
	struct io_uring_params params;
	unsigned int entries = 256;
	unsigned int cq_entries = 0;
	unsigned int iowq_bounded = 0;
	unsigned int iowq_unbounded = 0;
	int ret = 0;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$O&O&O&O&:Uring", kwlist,
					 conv_u32, &entries,
					 conv_u32, &cq_entries, conv_u32, &iowq_bounded,
					 conv_u32, &iowq_unbounded)) {
		return NULL;
	}

	if (entries == 0) {
		PyErr_SetString(PyExc_ValueError, "entries must be non-zero");
		return NULL;
	}

	self = (UringObject *)type->tp_alloc(type, 0);
	if (self == NULL) {
		return NULL;
	}

	self->ring_fd = -1;
	self->pool_free = URING_NO_SLOT;
	self->pool_hi = 0;

	self->pool = PyMem_RawCalloc(entries, sizeof(uring_op_t));
	if (self->pool == NULL) {
		PyErr_NoMemory();
		return NULL;
	}
	self->nr_pool = entries;

	memset(&params, 0, sizeof(params));

	/* COOP_TASKRUN (5.19+, always present on the 6.18 floor) cuts needless IPIs. */
	params.flags = IORING_SETUP_CLAMP | IORING_SETUP_COOP_TASKRUN;
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

	if (iowq_bounded > 0 || iowq_unbounded > 0) {
		unsigned int vals[2];
		vals[0] = iowq_bounded;
		vals[1] = iowq_unbounded;
		Py_BEGIN_ALLOW_THREADS
		ret = io_uring_register_iowq_max_workers(&self->ring, vals);
		Py_END_ALLOW_THREADS
		if (ret < 0) {
			uring_set_error(ret);
			goto fail;
		}
	}

	return (PyObject *)self;

fail:
	if (self->ring_ready) {
		io_uring_queue_exit(&self->ring);
		self->ring_ready = false;
	}
	PyMem_RawFree(self->pool);
	self->pool = NULL;
	Py_DECREF(self);
	return NULL;
}

static void
py_uring_ring_dealloc(UringObject *self)
{
	PyObject *exc = PyErr_GetRaisedException();

	uring_shutdown(self);

	/*
	 * uring_shutdown -> uring_drain may run Python signal handlers (its EINTR
	 * retry calls PyErr_CheckSignals) and so leave an exception set. tp_dealloc
	 * must not, so discard whatever it raised and restore the exception state we
	 * were entered with.
	 */
	PyErr_SetRaisedException(exc);
	Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(py_uring_ring_close__doc__,
"close($self)\n"
"--\n\n"
"Cancel every in-flight operation, drain the ring and release it. Idempotent.\n\n"
"Fail-closed against concurrent submitters: any submit()/cancel() racing close()\n"
"raises ValueError rather than touching freed state. Must not be called from a\n"
"completion callback (raises RuntimeError) -- close after reap() returns.\n\n"
"Buffers belonging to in-flight operations are held until their completions\n"
"reap. If the drain cannot complete, they are leaked rather than freed -- the\n"
"kernel may still be writing into them. Prepared-but-unsubmitted operations\n"
"have their buffers released immediately.\n");

static PyObject *
py_uring_ring_close(UringObject *self, PyObject *Py_UNUSED(ignored))
{
	/*
	 * A completion callback must not close() the ring: reap() is mid-drain, and
	 * tearing the ring down under it would free the pool the reap loop is walking.
	 * The RuntimeError is reported unraisably and the drain finishes normally;
	 * close after reap() returns.
	 */
	if (self->reaping) {
		PyErr_SetString(PyExc_RuntimeError,
				"cannot close() a Uring from within a completion "
				"callback; close after reap() returns");
		return NULL;
	}

	/*
	 * Idempotent, and taken before the lock: uring_shutdown marks the ring closed
	 * before it frees any op payload, so a __del__ that runs during this thread's
	 * own drain and calls close() again returns here instead of re-taking the
	 * non-recursive submit lock this thread still holds.
	 */
	if (self->closed) {
		Py_RETURN_NONE;
	}

	/*
	 * Fence submitters: take the submit lock so any in-flight submit()/cancel()
	 * has finished and no new one can start, then tear down while holding it.
	 * uring_shutdown marks the ring closed before it drains, so a submit that wakes
	 * on this lock afterward re-checks closed and bails with ValueError instead of
	 * touching the freed pool (fail-closed). The teardown's cancel/drain produces
	 * SQEs under this lock, so uring_drain does not take it itself -- holding it
	 * here already excludes every other producer.
	 */
	PyMutex_Lock(&self->submit_lock);
	uring_shutdown(self);
	PyMutex_Unlock(&self->submit_lock);

	if (PyErr_Occurred()) {
		/* A Python signal handler raised during the drain's EINTR retry. */
		return NULL;
	}
	Py_RETURN_NONE;
}

PyDoc_STRVAR(py_uring_ring_ringfd__doc__,
"ringfd($self)\n"
"--\n\n"
"Return the io_uring file descriptor.\n\n"
"Pollable: the kernel reports EPOLLIN whenever completions are pending, which\n"
"is what lets a higher layer drive reap() from an event loop with no eventfd.\n");

static PyObject *
py_uring_ring_ringfd(UringObject *self, PyObject *Py_UNUSED(ignored))
{
	if (self->closed || !self->ring_ready) {
		PyErr_SetString(PyExc_ValueError, "Uring is closed");
		return NULL;
	}
	return PyLong_FromLong(self->ring_fd);
}

static PyObject *
py_uring_ring_get_inflight(UringObject *self, void *Py_UNUSED(closure))
{
	return PyLong_FromUnsignedLong(self->inflight);
}

static PyObject *
py_uring_ring_get_closed(UringObject *self, void *Py_UNUSED(closure))
{
	return PyBool_FromLong(self->closed);
}

/* -- Uring method table ---------------------------------------------------- */

static PyMethodDef py_uring_ring_methods[] = {
	{
		.ml_name = "prep_openat2",
		.ml_meth = (PyCFunction)(void (*)(void))py_uring_ring_prep_openat2,
		.ml_flags = METH_FASTCALL | METH_KEYWORDS,
		.ml_doc = py_uring_ring_prep_openat2__doc__
	},
	{
		.ml_name = "prep_close",
		.ml_meth = (PyCFunction)(void (*)(void))py_uring_ring_prep_close,
		.ml_flags = METH_FASTCALL,
		.ml_doc = py_uring_ring_prep_close__doc__
	},
	{
		.ml_name = "prep_preadv2",
		.ml_meth = (PyCFunction)(void (*)(void))py_uring_ring_prep_preadv2,
		.ml_flags = METH_FASTCALL | METH_KEYWORDS,
		.ml_doc = py_uring_ring_prep_preadv2__doc__
	},
	{
		.ml_name = "prep_pwritev2",
		.ml_meth = (PyCFunction)(void (*)(void))py_uring_ring_prep_pwritev2,
		.ml_flags = METH_FASTCALL | METH_KEYWORDS,
		.ml_doc = py_uring_ring_prep_pwritev2__doc__
	},
	{
		.ml_name = "prep_fsync",
		.ml_meth = (PyCFunction)(void (*)(void))py_uring_ring_prep_fsync,
		.ml_flags = METH_FASTCALL | METH_KEYWORDS,
		.ml_doc = py_uring_ring_prep_fsync__doc__
	},
	{
		.ml_name = "prep_statx",
		.ml_meth = (PyCFunction)(void (*)(void))py_uring_ring_prep_statx,
		.ml_flags = METH_FASTCALL | METH_KEYWORDS,
		.ml_doc = py_uring_ring_prep_statx__doc__
	},
	{
		.ml_name = "submit",
		.ml_meth = (PyCFunction)(void (*)(void))py_uring_ring_submit,
		.ml_flags = METH_FASTCALL | METH_KEYWORDS,
		.ml_doc = py_uring_ring_submit__doc__
	},
	{
		.ml_name = "reap",
		.ml_meth = (PyCFunction)py_uring_ring_reap,
		.ml_flags = METH_VARARGS,
		.ml_doc = py_uring_ring_reap__doc__
	},
	{
		.ml_name = "cancel",
		.ml_meth = (PyCFunction)py_uring_ring_cancel,
		.ml_flags = METH_O,
		.ml_doc = py_uring_ring_cancel__doc__
	},
	{
		.ml_name = "close",
		.ml_meth = (PyCFunction)py_uring_ring_close,
		.ml_flags = METH_NOARGS,
		.ml_doc = py_uring_ring_close__doc__
	},
	{
		.ml_name = "ringfd",
		.ml_meth = (PyCFunction)py_uring_ring_ringfd,
		.ml_flags = METH_NOARGS,
		.ml_doc = py_uring_ring_ringfd__doc__
	},
	{ NULL, NULL, 0, NULL }
};

static PyGetSetDef py_uring_ring_getsetters[] = {
	{
		.name = "inflight",
		.get = (getter)py_uring_ring_get_inflight,
		.doc = "Number of operations currently in flight"
	},
	{
		.name = "closed",
		.get = (getter)py_uring_ring_get_closed,
		.doc = "True once close() has been called"
	},
	{ .name = NULL }
};

PyTypeObject UringType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_os.Uring",
	.tp_basicsize = sizeof(UringObject),
	.tp_dealloc = (destructor)py_uring_ring_dealloc,
	.tp_methods = py_uring_ring_methods,
	.tp_getset = py_uring_ring_getsetters,
	.tp_new = py_uring_ring_new,
	.tp_doc = uring__doc__,
	/*
	 * No Py_TPFLAGS_HAVE_GC: the Uring holds no Python object reachable
	 * through a C pointer. In-flight ops pin caller buffers, but a buffer
	 * never refers back to the ring, so the ring cannot be part of a
	 * reference cycle and needs no traverse/clear.
	 */
	.tp_flags = Py_TPFLAGS_DEFAULT,
};

/* -- registration --------------------------------------------------------- */

int
init_uring_types(PyObject *module)
{
	truenas_os_state_t *state = get_truenas_os_state(module);

	if (state == NULL) {
		return -1;
	}
	state->uring_repr_openat2 = PyUnicode_FromString("<truenas_os.UringOp openat2>");
	state->uring_repr_preadv2 = PyUnicode_FromString("<truenas_os.UringOp preadv2>");
	state->uring_repr_pwritev2 = PyUnicode_FromString("<truenas_os.UringOp pwritev2>");
	state->uring_repr_close = PyUnicode_FromString("<truenas_os.UringOp close>");
	state->uring_repr_statx = PyUnicode_FromString("<truenas_os.UringOp statx>");
	state->uring_repr_fsync = PyUnicode_FromString("<truenas_os.UringOp fsync>");
	if (!state->uring_repr_openat2 || !state->uring_repr_preadv2 ||
	    !state->uring_repr_pwritev2 || !state->uring_repr_close ||
	    !state->uring_repr_statx || !state->uring_repr_fsync) {
		return -1;
	}

	if (PyType_Ready(&UringType) < 0) {
		return -1;
	}
	if (PyType_Ready(&UringOpType) < 0) {
		return -1;
	}

	if (PyModule_AddObjectRef(module, "Uring", (PyObject *)&UringType) < 0) {
		return -1;
	}
	if (PyModule_AddObjectRef(module, "UringOp", (PyObject *)&UringOpType) < 0) {
		return -1;
	}

	return 0;
}
