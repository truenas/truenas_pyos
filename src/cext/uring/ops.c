// SPDX-License-Identifier: LGPL-3.0-or-later

/*
 * Operation submission.
 *
 * Every operation here is anchored and personality-stamped.  There is no
 * AT_FDCWD surface and no absolute-path surface: paths resolve against an
 * Anchor, and OPENAT2 defaults to RESOLVE_BENEATH.
 *
 * The op-table entry owns everything the kernel can see, from submission
 * until the CQE reaps -- buffers (pinned through a Py_buffer), path strings,
 * open_how and statx landing zones (PyMem_RawMalloc, because the kernel
 * writes into them while the GIL is not held).  A caller that loses interest
 * does not free anything; the entry is orphaned and released only when its
 * completion arrives.
 */

#include <Python.h>
#include "common/includes.h"
#include <linux/openat2.h>
#include "reactor.h"
#include "anchor.h"
#include "ops.h"

/* -- argument helpers ----------------------------------------------------- */

/*
 * Validate a Personality against this reactor.
 *
 * Personality is mandatory on every operation that consults credentials.
 * There is deliberately no "ambient" overload: an operation that silently
 * ran as the daemon must not be expressible (fs-reactor design section 5.4).
 */
static int
check_personality(ReactorObject *self, PyObject *obj, unsigned int *out)
{
	PersonalityObject *pers = NULL;

	if (!PyObject_TypeCheck(obj, &PersonalityType)) {
		PyErr_Format(PyExc_TypeError,
			     "personality must be a Personality, not %.200s",
			     Py_TYPE(obj)->tp_name);
		return -1;
	}

	pers = (PersonalityObject *)obj;
	if (pers->reactor != (PyObject *)self) {
		PyErr_SetString(PyExc_ValueError,
				"personality belongs to a different Reactor -- "
				"ids index that ring's table only");
		return -1;
	}
	if (pers->id == 0) {
		PyErr_SetString(PyExc_ValueError,
				"personality has been unregistered");
		return -1;
	}

	*out = pers->id;
	return 0;
}

static int
check_anchor(PyObject *obj, int *fd)
{
	AnchorObject *anchor = NULL;

	if (!Anchor_Check(obj)) {
		PyErr_Format(PyExc_TypeError,
			     "anchor must be an Anchor, not %.200s",
			     Py_TYPE(obj)->tp_name);
		return -1;
	}

	anchor = (AnchorObject *)obj;
	if (anchor->fd < 0) {
		PyErr_SetString(PyExc_ValueError, "Anchor is closed");
		return -1;
	}

	*fd = anchor->fd;
	return 0;
}

static int
check_file(ReactorObject *self, PyObject *obj, uint32_t *slot)
{
	FixedFileObject *file = NULL;

	if (!PyObject_TypeCheck(obj, &FixedFileType)) {
		PyErr_Format(PyExc_TypeError,
			     "file must be a FixedFile, not %.200s",
			     Py_TYPE(obj)->tp_name);
		return -1;
	}

	file = (FixedFileObject *)obj;
	if (file->reactor != (PyObject *)self) {
		PyErr_SetString(PyExc_ValueError,
				"FixedFile belongs to a different Reactor");
		return -1;
	}
	if (file->closed) {
		PyErr_SetString(PyExc_ValueError, "FixedFile is closed");
		return -1;
	}
	if (file->slot >= self->nr_files ||
	    !self->files[file->slot].in_use ||
	    self->files[file->slot].gen != file->gen) {
		PyErr_SetString(PyExc_ValueError,
				"FixedFile refers to a recycled slot");
		return -1;
	}

	*slot = file->slot;
	return 0;
}

/* -- submission ----------------------------------------------------------- */

/*
 * Finish a submission: create the Future, stamp user_data, submit.
 *
 * On any failure the op slot is released and NULL is returned with an
 * exception set -- nothing was submitted, so nothing is kernel-visible.
 */
static PyObject *
finish_submit(ReactorObject *self, uint32_t slot, struct io_uring_sqe *sqe,
	      unsigned int personality)
{
	uring_op_t *op = &self->ops[slot];
	PyObject *future = NULL;
	int ret = 0;

	future = uring_create_future(self->loop);
	if (future == NULL) {
		uring_op_release(self, slot);
		return NULL;
	}

	sqe->personality = (__u16)personality;
	io_uring_sqe_set_data64(sqe, URING_UD(op->tag, slot, op->gen));

	Py_BEGIN_ALLOW_THREADS
	ret = io_uring_submit(&self->ring);
	Py_END_ALLOW_THREADS

	if (ret < 0) {
		Py_DECREF(future);
		uring_op_release(self, slot);
		return uring_set_error(ret);
	}

	op->future = Py_NewRef(future);
	self->inflight++;

	if (op->file_slot != URING_NO_SLOT &&
	    op->file_slot < self->nr_files &&
	    self->files[op->file_slot].in_use) {
		self->files[op->file_slot].ops++;
	}

	return future;
}

/* -- open ----------------------------------------------------------------- */

const char Reactor_open__doc__[] =
"open(personality, anchor, path, flags=os.O_RDONLY, mode=0, resolve=RESOLVE_BENEATH)\n"
"--\n\n"
"Open a file into the registered file table, as `personality`.\n\n"
"This is the operation where the personality is genuinely load-bearing: the\n"
"kernel performs the whole path walk and the open permission check under the\n"
"snapshotted credentials, and an O_CREAT file is owned by that identity.\n"
"EACCES or ENOENT from here is the personality working, not a failure.\n\n"
"The file is installed at an explicit index reserved from this reactor's free\n"
"list; IORING_FILE_INDEX_ALLOC is never used. No process file descriptor is\n"
"created, so the result is a FixedFile, not an int.\n\n"
"Parameters\n"
"----------\n"
"personality : Personality\n"
"    Identity to perform the open as.\n"
"anchor : Anchor\n"
"    Directory the path resolves against.\n"
"path : str or bytes or os.PathLike\n"
"    Relative path. May contain multiple components -- OPENAT2 is the only\n"
"    operation that can, because RESOLVE_BENEATH confines it in-kernel.\n"
"flags : int\n"
"    open(2) flags. O_CLOEXEC is rejected: it is invalid with a fixed-file\n"
"    install and the kernel returns EINVAL.\n"
"mode : int\n"
"    Creation mode, used with O_CREAT. Note that it is masked by the reactor\n"
"    thread's umask, not the personality's -- struct cred carries no umask.\n"
"resolve : int\n"
"    RESOLVE_* flags. Defaults to RESOLVE_BENEATH.\n\n"
"Returns\n"
"-------\n"
"asyncio.Future\n"
"    Resolves to a FixedFile.\n";

PyObject *
Reactor_open(PyObject *op_self, PyObject *args, PyObject *kwargs)
{
	ReactorObject *self = (ReactorObject *)op_self;
	static char *kwlist[] = {
		"personality", "anchor", "path", "flags", "mode", "resolve", NULL
	};
	PyObject *pers_obj = NULL;
	PyObject *anchor_obj = NULL;
	PyObject *path_obj = NULL;
	PyObject *path_bytes = NULL;
	struct io_uring_sqe *sqe = NULL;
	uring_op_t *op = NULL;
	const char *path = NULL;
	Py_ssize_t path_len = 0;
	unsigned int personality = 0;
	uint32_t slot = URING_NO_SLOT;
	uint32_t file_slot = URING_NO_SLOT;
	int anchor_fd = -1;
	int flags = O_RDONLY;
	unsigned int mode = 0;
	unsigned long long resolve = RESOLVE_BENEATH;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OOO|iIK:open", kwlist,
					 &pers_obj, &anchor_obj, &path_obj,
					 &flags, &mode, &resolve)) {
		return NULL;
	}

	if (uring_check_ready(self) < 0) {
		return NULL;
	}
	if (check_personality(self, pers_obj, &personality) < 0) {
		return NULL;
	}
	if (check_anchor(anchor_obj, &anchor_fd) < 0) {
		return NULL;
	}

	if (flags & O_CLOEXEC) {
		PyErr_SetString(PyExc_ValueError,
				"O_CLOEXEC is invalid for a fixed-file install "
				"and the kernel rejects it with EINVAL");
		return NULL;
	}

	if (!PyUnicode_FSConverter(path_obj, &path_bytes)) {
		return NULL;
	}
	if (PyBytes_AsStringAndSize(path_bytes, (char **)&path, &path_len) < 0) {
		Py_DECREF(path_bytes);
		return NULL;
	}

	file_slot = uring_file_alloc(self);
	if (file_slot == URING_NO_SLOT) {
		Py_DECREF(path_bytes);
		PyErr_SetString(PyExc_OSError,
				"registered file table is full");
		return NULL;
	}

	slot = uring_op_alloc(self, URING_TAG_OPEN);
	if (slot == URING_NO_SLOT) {
		uring_file_release(self, file_slot);
		Py_DECREF(path_bytes);
		PyErr_SetString(PyExc_BlockingIOError,
				"operation table is full");
		return NULL;
	}

	op = &self->ops[slot];
	op->file_slot = file_slot;
	op->owns_slot = true;

	op->path = PyMem_RawMalloc((size_t)path_len + 1);
	op->how = PyMem_RawCalloc(1, sizeof(struct open_how));
	if (op->path == NULL || op->how == NULL) {
		uring_op_release(self, slot);
		uring_file_release(self, file_slot);
		Py_DECREF(path_bytes);
		return PyErr_NoMemory();
	}
	memcpy(op->path, path, (size_t)path_len + 1);
	Py_DECREF(path_bytes);

	op->how->flags = (__u64)flags;
	op->how->mode = (__u64)mode;
	op->how->resolve = (__u64)resolve;

	sqe = uring_get_sqe(self);
	if (sqe == NULL) {
		uring_op_release(self, slot);
		uring_file_release(self, file_slot);
		return NULL;
	}

	/* liburing offsets the index by one internally. */
	io_uring_prep_openat2_direct(sqe, anchor_fd, op->path, op->how,
				     file_slot);

	return finish_submit(self, slot, sqe, personality);
}

/* -- pread / pwrite ------------------------------------------------------- */

static PyObject *
reactor_rw(ReactorObject *self, PyObject *args, PyObject *kwargs, bool is_write)
{
	static char *kwlist[] = {
		"personality", "file", "buffer", "offset", NULL
	};
	PyObject *pers_obj = NULL;
	PyObject *file_obj = NULL;
	PyObject *buf_obj = NULL;
	struct io_uring_sqe *sqe = NULL;
	uring_op_t *op = NULL;
	unsigned long long offset = 0;
	unsigned int personality = 0;
	uint32_t slot = URING_NO_SLOT;
	uint32_t file_slot = URING_NO_SLOT;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OOO|K:rw", kwlist,
					 &pers_obj, &file_obj, &buf_obj,
					 &offset)) {
		return NULL;
	}

	if (uring_check_ready(self) < 0) {
		return NULL;
	}
	if (check_personality(self, pers_obj, &personality) < 0) {
		return NULL;
	}
	if (check_file(self, file_obj, &file_slot) < 0) {
		return NULL;
	}

	slot = uring_op_alloc(self,
			      is_write ? URING_TAG_WRITE : URING_TAG_READ);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError,
				"operation table is full");
		return NULL;
	}

	op = &self->ops[slot];
	op->file_slot = file_slot;

	/*
	 * Pin the buffer for the whole in-flight window. Holding a Py_buffer
	 * is what prevents a bytearray being resized under the kernel:
	 * bytearray's bf_getbuffer bumps ob_exports and resize then raises
	 * BufferError.
	 */
	if (PyObject_GetBuffer(buf_obj, &op->view,
			       is_write ? PyBUF_SIMPLE : PyBUF_WRITABLE) < 0) {
		uring_op_release(self, slot);
		return NULL;
	}
	op->has_view = true;
	op->buf_obj = Py_NewRef(buf_obj);

	sqe = uring_get_sqe(self);
	if (sqe == NULL) {
		uring_op_release(self, slot);
		return NULL;
	}

	if (is_write) {
		io_uring_prep_write(sqe, (int)file_slot, op->view.buf,
				    (unsigned int)op->view.len, offset);
	} else {
		io_uring_prep_read(sqe, (int)file_slot, op->view.buf,
				   (unsigned int)op->view.len, offset);
	}
	io_uring_sqe_set_flags(sqe, IOSQE_FIXED_FILE);

	return finish_submit(self, slot, sqe, personality);
}

const char Reactor_pread__doc__[] =
"pread(personality, file, buffer, offset=0)\n"
"--\n\n"
"Read into `buffer` at an explicit offset.\n\n"
"Positional, not dirfd-relative: the 'p' prefix follows pread(2). A registered\n"
"file exposes no file position, so every data operation takes an offset and\n"
"there is no seek.\n\n"
"The personality is stamped here for attribution rather than access control --\n"
"Unix decided access at open, and the registered file is the capability.\n\n"
"The buffer is pinned until the operation completes, including if the\n"
"awaiting task is cancelled. Do not resize it in the meantime.\n\n"
"Parameters\n"
"----------\n"
"personality : Personality\n"
"file : FixedFile\n"
"buffer : writable buffer\n"
"    bytearray, memoryview or anything supporting the writable buffer\n"
"    protocol.\n"
"offset : int\n\n"
"Returns\n"
"-------\n"
"asyncio.Future\n"
"    Resolves to the number of bytes read.\n";

PyObject *
Reactor_pread(PyObject *op_self, PyObject *args, PyObject *kwargs)
{
	ReactorObject *self = (ReactorObject *)op_self;
	return reactor_rw(self, args, kwargs, false);
}

const char Reactor_pwrite__doc__[] =
"pwrite(personality, file, buffer, offset=0)\n"
"--\n\n"
"Write `buffer` at an explicit offset.\n\n"
"The buffer is pinned until the operation completes, including if the\n"
"awaiting task is cancelled. Do not mutate it in the meantime.\n\n"
"Parameters\n"
"----------\n"
"personality : Personality\n"
"file : FixedFile\n"
"buffer : buffer\n"
"offset : int\n\n"
"Returns\n"
"-------\n"
"asyncio.Future\n"
"    Resolves to the number of bytes written.\n";

PyObject *
Reactor_pwrite(PyObject *op_self, PyObject *args, PyObject *kwargs)
{
	ReactorObject *self = (ReactorObject *)op_self;
	return reactor_rw(self, args, kwargs, true);
}

/* -- fsync ---------------------------------------------------------------- */

const char Reactor_fsync__doc__[] =
"fsync(personality, file, datasync=False)\n"
"--\n\n"
"Flush a registered file to stable storage.\n\n"
"Always executed by an io-wq worker: IORING_OP_FSYNC is force-async, so it\n"
"occupies a worker for its full duration.\n\n"
"Parameters\n"
"----------\n"
"personality : Personality\n"
"file : FixedFile\n"
"datasync : bool\n"
"    Use IORING_FSYNC_DATASYNC, skipping metadata that is not needed to read\n"
"    the data back.\n\n"
"Returns\n"
"-------\n"
"asyncio.Future\n"
"    Resolves to None.\n";

PyObject *
Reactor_fsync(PyObject *op_self, PyObject *args, PyObject *kwargs)
{
	ReactorObject *self = (ReactorObject *)op_self;
	static char *kwlist[] = {"personality", "file", "datasync", NULL};
	PyObject *pers_obj = NULL;
	PyObject *file_obj = NULL;
	struct io_uring_sqe *sqe = NULL;
	unsigned int personality = 0;
	uint32_t slot = URING_NO_SLOT;
	uint32_t file_slot = URING_NO_SLOT;
	int datasync = 0;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|p:fsync", kwlist,
					 &pers_obj, &file_obj, &datasync)) {
		return NULL;
	}

	if (uring_check_ready(self) < 0) {
		return NULL;
	}
	if (check_personality(self, pers_obj, &personality) < 0) {
		return NULL;
	}
	if (check_file(self, file_obj, &file_slot) < 0) {
		return NULL;
	}

	slot = uring_op_alloc(self, URING_TAG_FSYNC);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError, "operation table is full");
		return NULL;
	}
	self->ops[slot].file_slot = file_slot;

	sqe = uring_get_sqe(self);
	if (sqe == NULL) {
		uring_op_release(self, slot);
		return NULL;
	}

	io_uring_prep_fsync(sqe, (int)file_slot,
			    datasync ? IORING_FSYNC_DATASYNC : 0);
	io_uring_sqe_set_flags(sqe, IOSQE_FIXED_FILE);

	return finish_submit(self, slot, sqe, personality);
}

/* -- statx ---------------------------------------------------------------- */

const char Reactor_statx__doc__[] =
"statx(personality, anchor, leaf, mask=STATX_BASIC_STATS, flags=0)\n"
"--\n\n"
"Stat a single entry beneath `anchor`, as `personality`.\n\n"
"The personality is load-bearing here: resolving the leaf is a permission\n"
"check against the snapshotted credentials.\n\n"
"statx cannot name a registered file -- IORING_OP_STATX resolves a path only,\n"
"never a fixed-file index -- so there is no stat-an-open-FixedFile form.\n\n"
"`leaf` must be a single path component. The *at opcodes honour no RESOLVE_*\n"
"flags at all, so the single-component rule is the confinement.\n\n"
"Always executed by an io-wq worker: IORING_OP_STATX is unconditionally\n"
"force-async.\n\n"
"Parameters\n"
"----------\n"
"personality : Personality\n"
"anchor : Anchor\n"
"leaf : str or bytes or os.PathLike\n"
"    A single component: not empty, no '/', not '.' or '..'.\n"
"mask : int\n"
"    STATX_* field mask, from truenas_os. These are statx(2) constants, not\n"
"    io_uring ones, so they are not duplicated into this submodule.\n"
"flags : int\n"
"    AT_* flags from truenas_os, e.g. truenas_os.AT_SYMLINK_NOFOLLOW.\n\n"
"Returns\n"
"-------\n"
"asyncio.Future\n"
"    Resolves to a truenas_os.StatxResult.\n";

PyObject *
Reactor_statx(PyObject *op_self, PyObject *args, PyObject *kwargs)
{
	ReactorObject *self = (ReactorObject *)op_self;
	static char *kwlist[] = {
		"personality", "anchor", "leaf", "mask", "flags", NULL
	};
	PyObject *pers_obj = NULL;
	PyObject *anchor_obj = NULL;
	PyObject *leaf_obj = NULL;
	PyObject *leaf_owner = NULL;
	struct io_uring_sqe *sqe = NULL;
	uring_op_t *op = NULL;
	const char *leaf = NULL;
	Py_ssize_t leaf_len = 0;
	unsigned int personality = 0;
	unsigned int mask = STATX_BASIC_STATS;
	uint32_t slot = URING_NO_SLOT;
	int anchor_fd = -1;
	int flags = 0;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OOO|Ii:statx", kwlist,
					 &pers_obj, &anchor_obj, &leaf_obj,
					 &mask, &flags)) {
		return NULL;
	}

	if (uring_check_ready(self) < 0) {
		return NULL;
	}
	if (check_personality(self, pers_obj, &personality) < 0) {
		return NULL;
	}
	if (check_anchor(anchor_obj, &anchor_fd) < 0) {
		return NULL;
	}
	if (uring_leaf_convert(leaf_obj, &leaf_owner, &leaf, &leaf_len) < 0) {
		return NULL;
	}

	slot = uring_op_alloc(self, URING_TAG_STATX);
	if (slot == URING_NO_SLOT) {
		Py_DECREF(leaf_owner);
		PyErr_SetString(PyExc_BlockingIOError, "operation table is full");
		return NULL;
	}

	op = &self->ops[slot];
	op->path = PyMem_RawMalloc((size_t)leaf_len + 1);
	/* The kernel writes here at completion, off-GIL: raw allocation only. */
	op->stx = PyMem_RawCalloc(1, sizeof(struct statx));
	if (op->path == NULL || op->stx == NULL) {
		uring_op_release(self, slot);
		Py_DECREF(leaf_owner);
		return PyErr_NoMemory();
	}
	memcpy(op->path, leaf, (size_t)leaf_len + 1);
	Py_DECREF(leaf_owner);

	sqe = uring_get_sqe(self);
	if (sqe == NULL) {
		uring_op_release(self, slot);
		return NULL;
	}

	io_uring_prep_statx(sqe, anchor_fd, op->path, flags, mask, op->stx);

	return finish_submit(self, slot, sqe, personality);
}

/* -- close ---------------------------------------------------------------- */

const char Reactor_close_file__doc__[] =
"close_file(file)\n"
"--\n\n"
"Close a registered file and return its slot to the free list.\n\n"
"Takes no personality. This is one of the two exemptions from the\n"
"mandatory-personality rule: closing a fixed slot consults no credentials,\n"
"and the operation must remain stageable from finalisation, which can carry\n"
"no argument.\n\n"
"The close must be the file's last operation. A slot freed while another\n"
"operation is still in flight would leave that operation pinning the old file\n"
"under an index that has already been reused, so this raises instead.\n\n"
"Parameters\n"
"----------\n"
"file : FixedFile\n\n"
"Returns\n"
"-------\n"
"asyncio.Future\n"
"    Resolves to None.\n";

PyObject *
Reactor_close_file(PyObject *op_self, PyObject *file_obj)
{
	ReactorObject *self = (ReactorObject *)op_self;
	struct io_uring_sqe *sqe = NULL;
	uint32_t slot = URING_NO_SLOT;
	uint32_t file_slot = URING_NO_SLOT;

	if (uring_check_ready(self) < 0) {
		return NULL;
	}
	if (check_file(self, file_obj, &file_slot) < 0) {
		return NULL;
	}

	if (self->files[file_slot].ops > 0) {
		PyErr_Format(PyExc_BlockingIOError,
			     "FixedFile still has %u operation(s) in flight; "
			     "the slot-freeing close must be the file's last "
			     "operation",
			     self->files[file_slot].ops);
		return NULL;
	}

	slot = uring_op_alloc(self, URING_TAG_CLOSE);
	if (slot == URING_NO_SLOT) {
		PyErr_SetString(PyExc_BlockingIOError, "operation table is full");
		return NULL;
	}
	self->ops[slot].file_slot = file_slot;

	sqe = uring_get_sqe(self);
	if (sqe == NULL) {
		uring_op_release(self, slot);
		return NULL;
	}

	io_uring_prep_close_direct(sqe, file_slot);

	((FixedFileObject *)file_obj)->closed = true;

	/* personality 0: teardown consults no credentials. */
	return finish_submit(self, slot, sqe, 0);
}

