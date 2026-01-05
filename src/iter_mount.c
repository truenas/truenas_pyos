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
	if (reverse) {
		self->req.param |= LISTMOUNT_REVERSE;
	}

	self->current_idx = 0;
	self->statmount_flags = statmount_flags;

	// Fetch the first batch
	Py_BEGIN_ALLOW_THREADS
	count = syscall(__NR_listmount, &self->req, self->mnt_ids,
			LISTMOUNT_BATCH_SIZE, 0);
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

static PyObject *
mount_iter_next(MountIterator *self)
{
	ssize_t count;
	uint64_t mnt_id;

	// Check if we've exhausted the current batch
	if (self->current_idx >= self->batch_count) {
		// Only fetch more if the previous batch was full
		if (self->batch_count == LISTMOUNT_BATCH_SIZE) {
			// Update the request to continue from the last mount ID
			self->req.param = self->mnt_ids[self->batch_count - 1];
			if (self->req.param & LISTMOUNT_REVERSE) {
				// Preserve the reverse flag
				self->req.param = self->mnt_ids[self->batch_count - 1] | LISTMOUNT_REVERSE;
			}

			Py_BEGIN_ALLOW_THREADS
			count = syscall(__NR_listmount, &self->req, self->mnt_ids,
					LISTMOUNT_BATCH_SIZE, 0);
			Py_END_ALLOW_THREADS

			if (count < 0) {
				PyErr_SetFromErrno(PyExc_OSError);
				return NULL;
			}

			self->batch_count = count;
			self->current_idx = 0;

			// If no more results, we're done
			if (count == 0) {
				PyErr_SetNone(PyExc_StopIteration);
				return NULL;
			}
		} else {
			// Previous batch was partial, we're done
			PyErr_SetNone(PyExc_StopIteration);
			return NULL;
		}
	}

	// Get the next mount ID from the current batch
	mnt_id = self->mnt_ids[self->current_idx];
	self->current_idx++;

	// Call do_statmount to get the mount information
	return do_statmount(mnt_id, self->statmount_flags);
}

PyDoc_STRVAR(mount_iter__doc__,
"Iterator for mount information.\n\n"
"This iterator yields statmount() results for each mount under a\n"
"specified mount ID. It uses listmount(2) syscall to efficiently\n"
"retrieve mount IDs in batches, then yields StatmountResult objects\n"
"for each mount via statmount(2)."
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
