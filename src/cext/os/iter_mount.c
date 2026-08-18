// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "mount.h"
#include <linux/mount.h>
#include <sys/syscall.h>
#include <unistd.h>

#define __NR_listmount 458

typedef struct {
	PyObject_HEAD
	struct mnt_id_req req;          // listmount request structure
	uint64_t mnt_ids[LISTMOUNT_BATCH_SIZE];  // Buffer for mount IDs
	ssize_t batch_count;            // Number of IDs in current batch
	ssize_t current_idx;            // Current position in batch
	uint64_t statmount_flags;       // statmount mask for what fields to retrieve
	int reverse;                    // LISTMOUNT_REVERSE direction, kept across batches
} MountIterator;

static int
mount_iter_init(MountIterator *self, PyObject *args, PyObject *kwargs)
{
	uint64_t mnt_id = LSMT_ROOT;
	uint64_t last_mnt_id = 0;
	int reverse = 0;
	uint64_t statmount_flags = STATMOUNT_MNT_BASIC | STATMOUNT_SB_BASIC;
	const char *kwnames[] = { "mnt_id", "last_mnt_id", "reverse", "statmount_flags", NULL };
	ssize_t count;

	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|KKpK",
					 discard_const_p(char *, kwnames),
					 &mnt_id, &last_mnt_id, &reverse, &statmount_flags)) {
		return -1;
	}

	// Initialize the listmount request
	self->req.size = MNT_ID_REQ_SIZE_VER1;
	self->req.mnt_id = mnt_id;
	self->req.param = last_mnt_id;

	self->current_idx = 0;
	self->statmount_flags = statmount_flags;
	self->reverse = reverse;

	// Fetch the first batch
	Py_BEGIN_ALLOW_THREADS
	count = syscall(__NR_listmount, &self->req, self->mnt_ids,
			LISTMOUNT_BATCH_SIZE, self->reverse ? LISTMOUNT_REVERSE : 0);
	Py_END_ALLOW_THREADS

	if (count < 0) {
		PyErr_SetFromErrno(PyExc_OSError);
		return -1;
	}

	self->batch_count = count;

	return 0;
}

static PyObject *
mount_iter_iter(PyObject *self)
{
	Py_INCREF(self);
	return self;
}

/*
 * Take the next mount id from the current batch, fetching another batch from
 * listmount(2) once this one runs out.  Returns 1 with *mnt_id set, 0 when
 * there are no ids left (StopIteration set), or -1 on failure (error set).
 */
static int
mount_iter_next_id(MountIterator *self, uint64_t *mnt_id)
{
	ssize_t count;

	// Check if we've exhausted the current batch
	if (self->current_idx >= self->batch_count) {
		// Only fetch more if the previous batch was full
		if (self->batch_count != LISTMOUNT_BATCH_SIZE) {
			// Previous batch was partial, we're done
			PyErr_SetNone(PyExc_StopIteration);
			return 0;
		}

		// Continue from the last mount id of the previous batch.
		// The kernel takes direction from the flags argument, so
		// req.param carries only the cursor (a mount id), never
		// the reverse flag.
		self->req.param = self->mnt_ids[self->batch_count - 1];

		Py_BEGIN_ALLOW_THREADS
		count = syscall(__NR_listmount, &self->req, self->mnt_ids,
				LISTMOUNT_BATCH_SIZE,
				self->reverse ? LISTMOUNT_REVERSE : 0);
		Py_END_ALLOW_THREADS

		// A failure here concerns the mount we were asked to enumerate
		// rather than one of its children, so it is raised.  Notably
		// listmount(2) reports ENOENT once the mnt_id we are scoped to is
		// itself unmounted, and the iterator cannot continue from that.
		if (count < 0) {
			PyErr_SetFromErrno(PyExc_OSError);
			return -1;
		}

		self->batch_count = count;
		self->current_idx = 0;

		// If no more results, we're done
		if (count == 0) {
			PyErr_SetNone(PyExc_StopIteration);
			return 0;
		}
	}

	*mnt_id = self->mnt_ids[self->current_idx];
	self->current_idx++;

	return 1;
}

static PyObject *
mount_iter_next(MountIterator *self)
{
	PyObject *result = NULL;
	uint64_t mnt_id = 0;

	while (mount_iter_next_id(self, &mnt_id) == 1) {
		// Call do_statmount to get the mount information
		result = do_statmount(mnt_id, self->statmount_flags);
		if (result != NULL) {
			return result;
		}

		// listmount(2) hands back a batch of mount ids that are resolved one
		// by one afterwards, so a mount that goes away in between is reported
		// by statmount(2) as ENOENT, which reaches us as FileNotFoundError.
		// That is ordinary mount table churn -- ZFS snapshot automounts alone
		// expire on a timer -- and the mount is genuinely gone, so it is
		// skipped rather than failing the whole enumeration.  No other failure
		// in do_statmount() raises FileNotFoundError, so nothing else can be
		// mistaken for it.
		if (!PyErr_ExceptionMatches(PyExc_FileNotFoundError)) {
			return NULL;
		}

		PyErr_Clear();
	}

	// Out of mount ids: mount_iter_next_id() has set StopIteration, or the
	// error that ended the enumeration.
	return NULL;
}

PyDoc_STRVAR(mount_iter__doc__,
"Iterator for mount information.\n\n"
"This iterator yields statmount() results for each mount under a\n"
"specified mount ID. It uses listmount(2) syscall to efficiently\n"
"retrieve mount IDs in batches, then yields StatmountResult objects\n"
"for each mount via statmount(2).\n\n"
"A mount that is unmounted between the listmount(2) call that returned\n"
"its id and the statmount(2) call that resolves it is skipped."
);

static PyTypeObject MountIteratorType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_os.MountIterator",
	.tp_basicsize = sizeof(MountIterator),
	.tp_init = (initproc)mount_iter_init,
	.tp_new = PyType_GenericNew,
	.tp_flags = Py_TPFLAGS_DEFAULT,
	.tp_doc = mount_iter__doc__,
	.tp_iter = mount_iter_iter,
	.tp_iternext = (iternextfunc)mount_iter_next,
};

PyObject *create_mount_iterator(PyObject *args, PyObject *kwargs)
{
	return PyObject_Call((PyObject *)&MountIteratorType, args, kwargs);
}

int init_mount_iter_type(PyObject *module)
{
	if (PyType_Ready(&MountIteratorType) < 0) {
		return -1;
	}

	return 0;
}
