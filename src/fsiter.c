#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "fsiter.h"
#include "statx.h"
#include "openat2.h"
#include "mount.h"
#include "truenas_os_state.h"

#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <linux/mount.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>

/* Flags from Python version */
#define STATX_MASK_ITER (STATX_BASIC_STATS | STATX_BTIME | STATX_MNT_ID_UNIQUE)
#define STATX_FLAGS_ITER (AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW)
#define RESOLVE_FLAGS_ITER (RESOLVE_NO_XDEV | RESOLVE_NO_SYMLINKS)
#define OFLAGS_DIR_ITER (O_NOFOLLOW | O_DIRECTORY)

/* Error reporting macros */
#define __LOCATION__ __FILE__ ":" __stringify(__LINE__)
#define __stringify(x) __stringify_1(x)
#define __stringify_1(x) #x

#define SET_ERROR(err, fmt, ...) \
	snprintf((err)->message, sizeof((err)->message), \
			 "[%s] " fmt, __LOCATION__, ##__VA_ARGS__)

#define SET_ERROR_ERRNO(err, fmt, ...) \
	snprintf((err)->message, sizeof((err)->message), \
			 "[%s] " fmt ": %s", __LOCATION__, ##__VA_ARGS__, strerror(errno))

#define ISDOT(path) ( \
	*((const char *)(path)) == '.' && \
	*(((const char *)(path)) + 1) == '\0' \
)

#define ISDOTDOT(path)  ( \
	*((const char *)(path)) == '.' && \
	*(((const char *)(path)) + 1) == '.' && \
	*(((const char *)(path)) + 2) == '\0' \
)


/* Forward declarations */
static PyTypeObject FilesystemIteratorType;

/* IterInstance struct sequence indices */
enum {
	ITER_INST_PARENT = 0,
	ITER_INST_NAME,
	ITER_INST_FD,
	ITER_INST_STATX,
	ITER_INST_ISDIR,
	ITER_INST_NUM_FIELDS
};

/* IterInstance struct sequence type */
static PyStructSequence_Field iter_instance_fields[] = {
	{"parent", "Parent directory path"},
	{"name", "Entry name"},
	{"fd", "Open file descriptor"},
	{"statxinfo", "Statx result object"},
	{"isdir", "True if directory, False if file"},
	{NULL}
};

static PyStructSequence_Desc iter_instance_desc = {
	"truenas_os.IterInstance",
	"Filesystem iteration instance",
	iter_instance_fields,
	ITER_INST_NUM_FIELDS
};

/* FilesystemIterState struct sequence indices */
enum {
	STATE_CNT = 0,
	STATE_CNT_BYTES,
	STATE_CURRENT_DIRECTORY,
	STATE_NUM_FIELDS
};

static PyStructSequence_Field iter_state_fields[] = {
	{"cnt", "Count of items yielded"},
	{"cnt_bytes", "Total bytes of files yielded"},
	{"current_directory", "Current directory path"},
	{NULL}
};

static PyStructSequence_Desc iter_state_desc = {
	"truenas_os.FilesystemIterState",
	"Filesystem iteration state",
	iter_state_fields,
	STATE_NUM_FIELDS
};

/* Strdup, but using python memory allocator for accounting purposes */
static char *pymem_strdup(const char *str_in)
{
	char *str_out = NULL;
	size_t len = strlen(str_in);

	str_out = PyMem_RawMalloc(len + 1);
	if (str_out == NULL)
		return NULL;

	memcpy(str_out, str_in, len);
	str_out[len] = '\0';
	return str_out;
}

static inline void cleanup_iter_dir(iter_dir_t *iter)
{
	if (iter->dirp) {
		closedir(iter->dirp);
		iter->dirp = NULL;
	}

	PyMem_RawFree(iter->path);
	iter->path = NULL;
}

/*
 * Convert C iter_state_t to Python FilesystemIterState
 * Returns new reference, or NULL on error
 */
static PyObject *
py_fsiter_state_from_struct(const iter_state_t *c_state, const char *current_dir)
{
	PyObject *state_obj;
	truenas_os_state_t *state;

	state = get_truenas_os_state(NULL);
	if (state == NULL || state->FilesystemIterStateType == NULL) {
		PyErr_SetString(PyExc_SystemError, "FilesystemIterState type not initialized");
		return NULL;
	}

	state_obj = PyStructSequence_New((PyTypeObject *)state->FilesystemIterStateType);
	if (state_obj == NULL)
		return NULL;

	PyObject *cnt_obj = PyLong_FromSize_t(c_state->cnt);
	PyObject *cnt_bytes_obj = PyLong_FromSize_t(c_state->cnt_bytes);
	PyObject *current_dir_obj = PyUnicode_FromString(current_dir);

	if (!cnt_obj || !cnt_bytes_obj || !current_dir_obj) {
		Py_XDECREF(cnt_obj);
		Py_XDECREF(cnt_bytes_obj);
		Py_XDECREF(current_dir_obj);
		Py_DECREF(state_obj);
		return NULL;
	}

	PyStructSequence_SET_ITEM(state_obj, STATE_CNT, cnt_obj);
	PyStructSequence_SET_ITEM(state_obj, STATE_CNT_BYTES, cnt_bytes_obj);
	PyStructSequence_SET_ITEM(state_obj, STATE_CURRENT_DIRECTORY, current_dir_obj);

	return state_obj;
}

/*
 * Helper: Create IterInstance from fd, statx, parent path, and name
 */
static PyObject *
create_iter_instance(int fd, const struct statx *st, const char *parent, const char *name)
{
	PyObject *inst, *statx_obj, *parent_obj, *name_obj, *fd_obj, *isdir_obj;
	bool isdir;
	truenas_os_state_t *state;

	/* Determine if directory from statx mode */
	isdir = S_ISDIR(st->stx_mode);

	/* Convert statx struct to Python object */
	statx_obj = statx_to_pyobject(st);
	if (statx_obj == NULL)
		return NULL;

	/* Get module state */
	state = get_truenas_os_state(NULL);
	if (state == NULL || state->IterInstanceType == NULL) {
		Py_DECREF(statx_obj);
		PyErr_SetString(PyExc_SystemError, "IterInstance type not initialized");
		return NULL;
	}

	/* Create the struct sequence */
	inst = PyStructSequence_New((PyTypeObject *)state->IterInstanceType);
	if (inst == NULL) {
		Py_DECREF(statx_obj);
		return NULL;
	}

	/* Build fields */
	parent_obj = PyUnicode_FromString(parent);
	name_obj = PyUnicode_FromString(name);
	fd_obj = PyLong_FromLong(fd);
	isdir_obj = Py_NewRef(isdir ? Py_True : Py_False);

	if (!parent_obj || !name_obj || !fd_obj) {
		Py_XDECREF(parent_obj);
		Py_XDECREF(name_obj);
		Py_XDECREF(fd_obj);
		Py_DECREF(isdir_obj);
		Py_DECREF(statx_obj);
		Py_DECREF(inst);
		return NULL;
	}

	PyStructSequence_SET_ITEM(inst, ITER_INST_PARENT, parent_obj);
	PyStructSequence_SET_ITEM(inst, ITER_INST_NAME, name_obj);
	PyStructSequence_SET_ITEM(inst, ITER_INST_FD, fd_obj);
	PyStructSequence_SET_ITEM(inst, ITER_INST_STATX, statx_obj);
	PyStructSequence_SET_ITEM(inst, ITER_INST_ISDIR, isdir_obj);

	return inst;
}

/*
 * FilesystemIterator dealloc
 */
static void
FilesystemIterator_dealloc(FilesystemIteratorObject *self)
{
	size_t i;

	/* Clean up stack - close any open directories */
	for (i = 0; i < self->cur_depth; i++) {
		cleanup_iter_dir(&self->dir_stack[i]);
	}

	/* Clean up reporting callback references */
	Py_XDECREF(self->reporting_cb);
	Py_XDECREF(self->reporting_cb_private_data);

	/* Clean up cookies array */
	PyMem_RawFree(self->cookies);

	Py_TYPE(self)->tp_free((PyObject *)self);
}

/*
 * FilesystemIterator __iter__
 */
static PyObject *
FilesystemIterator_iter(PyObject *self)
{
	Py_INCREF(self);
	return self;
}

/* Iteration action codes */
enum fsiter_action {
	FSITER_YIELD_FILE = 0,      /* Yield file to Python */
	FSITER_CONTINUE = 1,        /* Continue to next entry */
	FSITER_YIELD_DIR = 2,       /* Yield directory to push */
	FSITER_POP_DIR = 3,         /* Pop directory from stack */
	FSITER_ERROR = -1           /* Error occurred */
};

/*
 * Process next directory entry
 * Called with GIL released
 * Returns action code, fills self->last on success
 */
static enum fsiter_action
process_next_entry(FilesystemIteratorObject *self,
		   iter_dir_t *cur_dir,
		   struct dirent *entry,
		   fsiter_error_t *err)
{
	int fd, ret;
	struct statx st;
	bool is_dir;
	int open_flags;

	/* Store directory entry name */
	strlcpy(self->last.name, entry->d_name, sizeof(self->last.name));

	/* Open entry with openat2 */
	is_dir = (entry->d_type == DT_DIR);
	open_flags = is_dir ? OFLAGS_DIR_ITER : self->state.file_open_flags;

	fd = openat2_impl(dirfd(cur_dir->dirp), entry->d_name, open_flags, RESOLVE_FLAGS_ITER);
	if (fd < 0) {
		/* ELOOP: intermediate component replaced with symlink (shouldn't be possible)
		 * EXDEV: entry is on a different filesystem (crossed mount boundary)
		 * In both cases, prune this branch and continue iteration.
		 */
		if (errno == ELOOP || errno == EXDEV) {
			return FSITER_CONTINUE;
		}
		SET_ERROR_ERRNO(err, "openat2(%s)", entry->d_name);
		return FSITER_ERROR;
	}

	/* Call statx on fd */
	ret = statx_impl(fd, "", STATX_FLAGS_ITER, STATX_MASK_ITER, &st);
	if (ret < 0) {
		SET_ERROR_ERRNO(err, "statx(%s)", entry->d_name);
		close(fd);
		return FSITER_ERROR;
	}

	/* Check btime cutoff for files - skip files NEWER than cutoff */
	if (!S_ISDIR(st.stx_mode) && self->state.btime_cutoff) {
		if (st.stx_btime.tv_sec > self->state.btime_cutoff) {
			close(fd);
			return FSITER_CONTINUE;
		}
	}

	/* Fill last entry */
	self->last.fd = fd;
	memcpy(&self->last.st, &st, sizeof(struct statx));
	self->last.is_dir = S_ISDIR(st.stx_mode);

	return self->last.is_dir ? FSITER_YIELD_DIR : FSITER_YIELD_FILE;
}

/*
 * Push directory onto stack
 * Called with GIL released
 * self->last must be filled with directory entry
 */
static bool
push_dir_stack(FilesystemIteratorObject *self, iter_dir_t *cur_dir, fsiter_error_t *err)
{
	iter_dir_t *new_dir;
	DIR *dirp;
	int dup_fd;
	char full_path[PATH_MAX];

	/* Build full path for the directory */
	snprintf(full_path, sizeof(full_path), "%s/%s", cur_dir->path, self->last.name);

	/* Check depth limit */
	if (self->cur_depth >= MAX_DEPTH) {
		SET_ERROR(err, "max depth %d exceeded at %s", MAX_DEPTH, full_path);
		return false;
	}

	/* Duplicate fd since the original will be returned to Python and closed by them */
	dup_fd = dup(self->last.fd);
	if (dup_fd < 0) {
		SET_ERROR_ERRNO(err, "dup(%s)", full_path);
		return false;
	}

	/* Open DIR* from duplicated fd - fdopendir takes ownership */
	dirp = fdopendir(dup_fd);
	if (dirp == NULL) {
		SET_ERROR_ERRNO(err, "fdopendir(%s)", full_path);
		close(dup_fd);
		return false;
	}

	/* Allocate path string */
	new_dir = &self->dir_stack[self->cur_depth];
	new_dir->path = pymem_strdup(full_path);
	if (new_dir->path == NULL) {
		SET_ERROR(err, "strdup failed for %s", full_path);
		closedir(dirp);  /* dirp not yet assigned to new_dir, close it here */
		return false;
	}

	new_dir->dirp = dirp;
	new_dir->ino = self->last.st.stx_ino;
	self->cur_depth++;

	return true;
}

/*
 * Pop directory from stack
 * Called with GIL released
 */
static bool
pop_dir_stack(FilesystemIteratorObject *self, fsiter_error_t *err)
{
	iter_dir_t *dir;

	if (self->cur_depth == 0) {
		return true;  /* Nothing to pop */
	}

	dir = &self->dir_stack[self->cur_depth - 1];

	cleanup_iter_dir(dir);
	self->cur_depth--;
	return true;
}

/*
 * Helper: build directory stack tuple
 * Returns new reference, or NULL on error
 */
static PyObject *
build_dir_stack_tuple(FilesystemIteratorObject *self)
{
	PyObject *stack_tuple;
	size_t i;

	/* Create a tuple to hold the directory tuples */
	stack_tuple = PyTuple_New(self->cur_depth);
	if (stack_tuple == NULL)
		return NULL;

	/* Add each directory as (path, inode) tuple */
	for (i = 0; i < self->cur_depth; i++) {
		PyObject *tuple, *path_obj, *inode_obj;

		/* Create tuple (path, inode) */
		tuple = PyTuple_New(2);
		if (tuple == NULL) {
			Py_DECREF(stack_tuple);
			return NULL;
		}

		path_obj = PyUnicode_FromString(self->dir_stack[i].path);
		inode_obj = PyLong_FromUnsignedLongLong(self->dir_stack[i].ino);

		if (path_obj == NULL || inode_obj == NULL) {
			Py_XDECREF(path_obj);
			Py_XDECREF(inode_obj);
			Py_DECREF(tuple);
			Py_DECREF(stack_tuple);
			return NULL;
		}

		PyTuple_SET_ITEM(tuple, 0, path_obj);
		PyTuple_SET_ITEM(tuple, 1, inode_obj);
		PyTuple_SET_ITEM(stack_tuple, i, tuple);
	}

	return stack_tuple;
}

/*
 * Helper: convert dir_stack tuple to cookies array
 * Returns true on success, false on error (with Python exception set)
 * On success, *cookies_out points to allocated array (caller must free with PyMem_RawFree)
 * and *cookie_sz_out contains the array size
 * On success with no dir_stack, *cookies_out is NULL and *cookie_sz_out is 0
 */
static bool
dir_stack_to_cookies(PyObject *dir_stack, uint64_t **cookies_out, size_t *cookie_sz_out)
{
	uint64_t *cookies = NULL;
	size_t cookie_sz;

	if (dir_stack == NULL || dir_stack == Py_None) {
		*cookies_out = NULL;
		*cookie_sz_out = 0;
		return true;
	}

	if (!PyTuple_Check(dir_stack)) {
		PyErr_SetString(PyExc_TypeError, "dir_stack must be a tuple");
		return false;
	}

	cookie_sz = PyTuple_GET_SIZE(dir_stack);
	if (cookie_sz == 0) {
		*cookies_out = NULL;
		*cookie_sz_out = 0;
		return true;
	}

	cookies = PyMem_RawMalloc(cookie_sz * sizeof(uint64_t));
	if (cookies == NULL) {
		PyErr_NoMemory();
		return false;
	}

	/* Extract inode numbers from dir_stack tuples */
	for (size_t i = 0; i < cookie_sz; i++) {
		PyObject *entry = PyTuple_GET_ITEM(dir_stack, i);
		if (!PyTuple_Check(entry) || PyTuple_GET_SIZE(entry) != 2) {
			PyMem_RawFree(cookies);
			PyErr_SetString(PyExc_ValueError,
				"dir_stack entries must be (path, inode) tuples");
			return false;
		}

		PyObject *inode_obj = PyTuple_GET_ITEM(entry, 1);
		if (!PyLong_Check(inode_obj)) {
			PyMem_RawFree(cookies);
			PyErr_SetString(PyExc_TypeError,
				"dir_stack inode must be an integer");
			return false;
		}

		cookies[i] = PyLong_AsUnsignedLongLong(inode_obj);
		if (cookies[i] == (uint64_t)-1 && PyErr_Occurred()) {
			PyMem_RawFree(cookies);
			return false;
		}
	}

	*cookies_out = cookies;
	*cookie_sz_out = cookie_sz;
	return true;
}

/*
 * Helper to invoke reporting callback if needed
 * Returns true on success, false on error (with Python exception set)
 */
static bool
check_and_invoke_reporting_callback(FilesystemIteratorObject *self, const char *current_dir)
{
	PyObject *state_obj, *dir_stack_obj;
	PyObject *callback_result;

	if (self->reporting_cb != NULL &&
		self->reporting_cb_increment &&
		(self->state.cnt % self->reporting_cb_increment) == 0) {

		/* Build dir_stack tuple */
		dir_stack_obj = build_dir_stack_tuple(self);
		if (dir_stack_obj == NULL)
			return false;

		state_obj = py_fsiter_state_from_struct(&self->state, current_dir);
		if (state_obj == NULL) {
			Py_DECREF(dir_stack_obj);
			return false;
		}

		callback_result = PyObject_CallFunctionObjArgs(
			self->reporting_cb,
			dir_stack_obj,
			state_obj,
			self->reporting_cb_private_data ? self->reporting_cb_private_data : Py_None,
			NULL
		);
		Py_DECREF(dir_stack_obj);
		Py_DECREF(state_obj);

		if (callback_result == NULL)
			return false;

		Py_DECREF(callback_result);
	}

	return true;
}

/*
 * Set IteratorRestoreError exception with depth and path attributes.
 * This is called when restoration fails because a directory in the saved
 * path no longer exists or has a different inode.
 */
static void
set_iterator_restore_error(size_t depth, const char *path)
{
	truenas_os_state_t *state;
	PyObject *errmsg, *exc, *depth_obj, *path_obj;

	FSITER_ASSERT(path != NULL, "path is NULL in set_iterator_restore_error");

	state = get_truenas_os_state(NULL);
	if (state == NULL || state->IteratorRestoreError == NULL) {
		PyErr_SetString(PyExc_SystemError,
			"Module state not initialized for IteratorRestoreError");
		return;
	}

	errmsg = PyUnicode_FromFormat(
		"Failed to restore iterator position at depth %zu in directory: %s",
		depth, path);
	if (errmsg == NULL) {
		return;
	}

	exc = PyObject_CallFunction(state->IteratorRestoreError, "O", errmsg);
	Py_DECREF(errmsg);

	if (exc == NULL) {
		return;
	}

	depth_obj = PyLong_FromSize_t(depth);
	if (depth_obj == NULL) {
		Py_DECREF(exc);
		return;
	}
	if (PyObject_SetAttrString(exc, "depth", depth_obj) < 0) {
		Py_DECREF(depth_obj);
		Py_DECREF(exc);
		return;
	}
	Py_DECREF(depth_obj);

	path_obj = PyUnicode_FromString(path);
	if (path_obj == NULL) {
		Py_DECREF(exc);
		return;
	}
	if (PyObject_SetAttrString(exc, "path", path_obj) < 0) {
		Py_DECREF(path_obj);
		Py_DECREF(exc);
		return;
	}
	Py_DECREF(path_obj);

	PyErr_SetObject(state->IteratorRestoreError, exc);
	Py_DECREF(exc);
}

/*
 * FilesystemIterator __next__
 */
static PyObject *
FilesystemIterator_next(FilesystemIteratorObject *self)
{
	enum fsiter_action action;
	PyObject *result;
	bool push_ok;
	struct dirent *direntp;
	iter_dir_t *cur_dir;
	int async_err = 0;
	size_t pos;

	/* Close fd from previous iteration */
	if (self->last.fd >= 0) {
		close(self->last.fd);
		self->last.fd = -1;
	}

	/* Handle skip() - pop directory if skip was requested */
	if (self->skip_next_recursion) {
		self->skip_next_recursion = false;
		if (self->cur_depth > 0) {
			Py_BEGIN_ALLOW_THREADS
			pop_dir_stack(self, &self->cerr);
			Py_END_ALLOW_THREADS
		}
	}

	/* Main iteration loop */
	while (self->cur_depth > 0) {
		FSITER_ASSERT(self->cur_depth < MAX_DEPTH,
		              "Iterator depth exceeded MAX_DEPTH");

		pos = self->cur_depth -1;
		cur_dir = &self->dir_stack[pos];

		/*
		 * We separate out the readdir call from the
		 * process_next_entry call so that handling for EINTR
		 * doesn't change our position in DIR
		 */
		Py_BEGIN_ALLOW_THREADS
		errno = 0;
		direntp = readdir(cur_dir->dirp);
		Py_END_ALLOW_THREADS

		if (direntp == NULL) {
			if (errno != 0) {
				PyErr_Format(PyExc_OSError, "readdir(%s) failed: %s",
					     cur_dir->path, strerror(errno));
				return NULL;
			}

			/*
			 * If we exhausted this directory but still have an
			 * unfulfilled cookie for this depth, we failed to
			 * restore the iterator state.
			 */
			if (self->cookies && (self->cur_depth < self->cookie_sz) &&
			    (self->cookies[self->cur_depth] != 0)) {
				set_iterator_restore_error(self->cur_depth, cur_dir->path);
				return NULL;
			}
			/* Directory exhausted */
			action = FSITER_POP_DIR;
		} else if (ISDOT(direntp->d_name) || ISDOTDOT(direntp->d_name)) {
			/* skip . and .. */
			action = FSITER_CONTINUE;
		} else {
			/*
			 * COOKIE NOM NOM
			 *
			 * If we're restoring from a previous iterator state,
			 * we have a "cookie" (inode number) for the directory
			 * we need to descend into at this depth. Skip all
			 * entries until we find the one matching our cookie.
			 *
			 * cookies[0] is root (which we start in), cookies[1]
			 * is the first subdir to descend into, etc. Since
			 * pos = cur_depth - 1, and we start with cur_depth = 1,
			 * we need to check cookies[cur_depth] to find the next
			 * directory to descend into.
			 */
			if (self->cookies && (self->cur_depth < self->cookie_sz)) {
				uint64_t mycookie = self->cookies[self->cur_depth];
				if (mycookie != 0) {
					if (direntp->d_ino != mycookie) {
						/* Not the entry we're looking for, skip it */
						action = FSITER_CONTINUE;
						continue;
					}
					/* Found matching cookie - clear it */
					self->cookies[self->cur_depth] = 0;
				}
			}

			/*
			 * Process next entry with GIL released
			 * Retry on EINTR unless python has handled a signal.
			 */
			do {
				Py_BEGIN_ALLOW_THREADS
				action = process_next_entry(self, cur_dir, direntp, &self->cerr);
				Py_END_ALLOW_THREADS
			} while (
				(action == FSITER_ERROR) &&
				(errno == EINTR) &&
				!(async_err = PyErr_CheckSignals())
			);
		}

		switch (action) {
		case FSITER_ERROR:
			/* Convert error buffer to Python exception */
			PyErr_SetString(PyExc_OSError, self->cerr.message);
			return NULL;

		case FSITER_CONTINUE:
			continue;

		case FSITER_YIELD_FILE:
			/* Create IterInstance for file */
			result = create_iter_instance(self->last.fd, &self->last.st,
						      cur_dir->path, self->last.name);
			if (result == NULL)
				return NULL;

			/* Update counters */
			self->state.cnt++;
			self->state.cnt_bytes += self->last.st.stx_size;

			/* Invoke reporting callback if needed */
			if (!check_and_invoke_reporting_callback(self, cur_dir->path)) {
				Py_DECREF(result);
				return NULL;
			}

			return result;

		case FSITER_YIELD_DIR:
			/* Create IterInstance for directory (unless restoring from cookie) */
			result = NULL;
			if (!self->restoring_from_cookie) {
				result = create_iter_instance(self->last.fd, &self->last.st,
							      cur_dir->path, self->last.name);
				if (result == NULL)
					return NULL;
			}

			/* Push directory onto stack with GIL released */
			Py_BEGIN_ALLOW_THREADS
			push_ok = push_dir_stack(self, cur_dir, &self->cerr);
			Py_END_ALLOW_THREADS

			if (!push_ok) {
				Py_XDECREF(result);
				PyErr_SetString(PyExc_OSError, self->cerr.message);
				return NULL;
			} else if (self->restoring_from_cookie) {
				/*
				 * At this point we know that we've hit our target
				 * for restoration from cookie, *BUT* we don't want
				 * to yield the directory to the consumer. The
				 * guarantee is that we will begin yielding inside
				 * the directory.
				 */
				if (self->cur_depth >= self->cookie_sz) {
					self->restoring_from_cookie = false;

					PyMem_RawFree(self->cookies);
					self->cookies = NULL;
					self->cookie_sz = 0;
				}
				Py_XDECREF(result);
				continue;
			}

			/* Update counter */
			self->state.cnt++;

			/* Invoke reporting callback if needed */
			if (!check_and_invoke_reporting_callback(self, cur_dir->path)) {
				Py_DECREF(result);
				return NULL;
			}

			return result;

		case FSITER_POP_DIR:
			/* Pop directory from stack with GIL released */
			Py_BEGIN_ALLOW_THREADS
			pop_dir_stack(self, &self->cerr);
			Py_END_ALLOW_THREADS

			/* Note: we don't fail on pop errors, just continue */
			continue;
		}
	}

	/* Stack exhausted - iteration complete */
	PyErr_SetNone(PyExc_StopIteration);
	return NULL;
}

/*
 * FilesystemIterator.get_stats() - return current iteration statistics
 */
PyDoc_STRVAR(FilesystemIterator_get_stats__doc__,
"get_stats()\n"
"--\n\n"
"Return current iteration statistics.\n\n"
"Returns a FilesystemIterState object containing:\n"
"  - cnt: Number of items yielded so far\n"
"  - cnt_bytes: Total bytes of files yielded\n"
"  - current_directory: Current directory path\n"
);

static PyObject *
FilesystemIterator_get_stats(FilesystemIteratorObject *self, PyObject *Py_UNUSED(ignored))
{
	const char *current_dir;

	/* Get current directory from stack top, or empty string if exhausted */
	if (self->cur_depth > 0) {
		current_dir = self->dir_stack[self->cur_depth - 1].path;
	} else {
		current_dir = "";
	}

	return py_fsiter_state_from_struct(&self->state, current_dir);
}

/*
 * FilesystemIterator.skip() - skip recursion into current directory
 */
PyDoc_STRVAR(FilesystemIterator_skip__doc__,
"skip()\n"
"--\n\n"
"Skip recursion into the currently yielded directory.\n\n"
"This method must be called immediately after the iterator yields a directory,\n"
"and before calling next() again. It prevents the iterator from recursing into\n"
"the directory that was just yielded.\n\n"
"Raises ValueError if the last yielded item was not a directory.\n"
);

static PyObject *
FilesystemIterator_skip(FilesystemIteratorObject *self, PyObject *Py_UNUSED(ignored))
{
	/* Verify that the last yielded item was a directory */
	if (!self->last.is_dir) {
		PyErr_SetString(PyExc_ValueError,
				"skip() can only be called when the last yielded item was a directory");
		return NULL;
	}

	/* Set flag to skip recursion on next __next__ call */
	self->skip_next_recursion = true;

	Py_RETURN_NONE;
}

/*
 * FilesystemIterator.dir_stack() - return current directory stack
 */
PyDoc_STRVAR(FilesystemIterator_dir_stack__doc__,
"dir_stack()\n"
"--\n\n"
"Return the current directory stack as a tuple of (path, inode) tuples.\n\n"
"Returns a tuple of tuples where each tuple contains:\n"
"  - path (str): The full directory path\n"
"  - inode (int): The inode number of the directory\n\n"
"The first element is the root directory, and the last element is the\n"
"current directory being processed.\n\n"
"Returns an empty tuple if iteration has completed.\n"
);

static PyObject *
FilesystemIterator_dir_stack(FilesystemIteratorObject *self, PyObject *Py_UNUSED(ignored))
{
	return build_dir_stack_tuple(self);
}

/* FilesystemIterator methods */
static PyMethodDef FilesystemIterator_methods[] = {
	{
		.ml_name = "get_stats",
		.ml_meth = (PyCFunction)FilesystemIterator_get_stats,
		.ml_flags = METH_NOARGS,
		.ml_doc = FilesystemIterator_get_stats__doc__
	},
	{
		.ml_name = "skip",
		.ml_meth = (PyCFunction)FilesystemIterator_skip,
		.ml_flags = METH_NOARGS,
		.ml_doc = FilesystemIterator_skip__doc__
	},
	{
		.ml_name = "dir_stack",
		.ml_meth = (PyCFunction)FilesystemIterator_dir_stack,
		.ml_flags = METH_NOARGS,
		.ml_doc = FilesystemIterator_dir_stack__doc__
	},
	{ .ml_name = NULL }  /* Sentinel */
};

/*
 * FilesystemIterator type definition
 */
static PyTypeObject FilesystemIteratorType = {
	PyVarObject_HEAD_INIT(NULL, 0)
	.tp_name = "truenas_os.FilesystemIterator",
	.tp_doc = "Filesystem iterator object",
	.tp_basicsize = sizeof(FilesystemIteratorObject),
	.tp_itemsize = 0,
	.tp_flags = Py_TPFLAGS_DEFAULT,
	.tp_dealloc = (destructor)FilesystemIterator_dealloc,
	.tp_iter = FilesystemIterator_iter,
	.tp_iternext = (iternextfunc)FilesystemIterator_next,
	.tp_methods = FilesystemIterator_methods,
};

/*
 * Create filesystem iterator - to be called from truenas_pyos.c
 */
PyObject *
create_filesystem_iterator(const char *mountpoint, const char *relative_path,
			   const char *filesystem_name, const iter_state_t *state,
			   size_t reporting_cb_increment,
			   PyObject *reporting_cb,
			   PyObject *reporting_cb_private_data,
			   PyObject *dir_stack)
{
	FilesystemIteratorObject *iter;
	int root_fd;
	struct statx root_st;
	int ret;
	char root_path[PATH_MAX];
	DIR *root_dirp;
	iter_dir_t *root_dir;
	struct statmount *sm;
	const char *sb_source;
	uint64_t *cookies = NULL;
	size_t cookie_sz = 0;

	/* Validate callback early before allocating resources */
	if (reporting_cb != NULL && reporting_cb != Py_None) {
		if (!PyCallable_Check(reporting_cb)) {
			PyErr_SetString(PyExc_TypeError, "reporting_callback must be callable");
			return NULL;
		}
	}

	/* Convert dir_stack to cookies array if provided */
	if (!dir_stack_to_cookies(dir_stack, &cookies, &cookie_sz)) {
		return NULL;
	}

	/* Build root path */
	int root_path_len;
	if (relative_path && relative_path[0] != '\0') {
		root_path_len = snprintf(root_path, sizeof(root_path), "%s/%s", mountpoint, relative_path);
	} else {
		root_path_len = snprintf(root_path, sizeof(root_path), "%s", mountpoint);
	}
	if (root_path_len >= (int)sizeof(root_path)) {
		PyMem_RawFree(cookies);
		PyErr_Format(PyExc_ValueError, "path too long (would be %d bytes)", root_path_len);
		return NULL;
	}

	/* Open root directory with openat2 */
	root_fd = openat2_impl(AT_FDCWD, root_path, OFLAGS_DIR_ITER, RESOLVE_NO_SYMLINKS);
	if (root_fd < 0) {
		PyMem_RawFree(cookies);
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, root_path);
		return NULL;
	}

	/* Call statx on root to validate mount */
	ret = statx_impl(root_fd, "", STATX_FLAGS_ITER, STATX_MASK_ITER, &root_st);
	if (ret < 0) {
		close(root_fd);
		PyMem_RawFree(cookies);
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, root_path);
		return NULL;
	}

	/* Validate it's a directory */
	if (!S_ISDIR(root_st.stx_mode)) {
		close(root_fd);
		PyMem_RawFree(cookies);
		PyErr_Format(PyExc_NotADirectoryError, "Not a directory: %s", root_path);
		return NULL;
	}

#ifdef STATMOUNT_SB_SOURCE
	/* Validate mount source using statmount */
	sm = statmount_impl(root_st.stx_mnt_id, STATMOUNT_SB_BASIC | STATMOUNT_SB_SOURCE);
	if (sm == NULL) {
		close(root_fd);
		PyMem_RawFree(cookies);
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, root_path);
		return NULL;
	}

	sb_source = sm->str + sm->sb_source;
	if (strcmp(sb_source, filesystem_name) != 0) {
		PyMem_RawFree(sm);
		close(root_fd);
		PyMem_RawFree(cookies);
		PyErr_Format(PyExc_RuntimeError,
				     "%s: filesystem source mismatch (expected %s, got %s)",
				     root_path, filesystem_name, sb_source);
		return NULL;
	}

	PyMem_RawFree(sm);
#endif /* STATMOUNT_SB_SOURCE */

	/* Open DIR* from root fd */
	root_dirp = fdopendir(root_fd);
	if (root_dirp == NULL) {
		close(root_fd);
		PyMem_RawFree(cookies);
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, root_path);
		return NULL;
	}

	/* Create iterator object */
	iter = PyObject_New(FilesystemIteratorObject, &FilesystemIteratorType);
	if (iter == NULL) {
		closedir(root_dirp);
		PyMem_RawFree(cookies);
		return NULL;
	}

	/* Initialize iterator fields */
	memset(iter->dir_stack, 0, sizeof(iter->dir_stack));
	memset(&iter->last, 0, sizeof(iter->last));
	iter->last.fd = -1;
	iter->cur_depth = 0;
	iter->skip_next_recursion = false;

	/* Copy state into iterator */
	memcpy(&iter->state, state, sizeof(iter_state_t));

	/* Initialize cookies */
	iter->cookies = cookies;
	iter->cookie_sz = cookie_sz;
	iter->restoring_from_cookie = cookies != NULL;

	/* Initialize reporting fields */
	iter->reporting_cb_increment = reporting_cb_increment;

	/* Store callback - normalize Py_None to NULL */
	if (reporting_cb != NULL && reporting_cb != Py_None) {
		iter->reporting_cb = Py_NewRef(reporting_cb);
	} else {
		iter->reporting_cb = NULL;
	}

	/* Store private data - normalize Py_None to NULL */
	if (reporting_cb_private_data != NULL && reporting_cb_private_data != Py_None) {
		iter->reporting_cb_private_data = Py_NewRef(reporting_cb_private_data);
	} else {
		iter->reporting_cb_private_data = NULL;
	}

	/* Initialize stack with root directory */
	root_dir = &iter->dir_stack[0];
	root_dir->path = pymem_strdup(root_path);
	if (root_dir->path == NULL) {
		closedir(root_dirp);
		Py_DECREF(iter);
		return PyErr_NoMemory();
	}

	root_dir->dirp = root_dirp;
	root_dir->ino = root_st.stx_ino;
	iter->cur_depth = 1;

	return (PyObject *)iter;
}

/*
 * Initialize iterator types and add to module
 */
int
init_iter_types(PyObject *module)
{
	truenas_os_state_t *state = get_truenas_os_state(module);
	if (state == NULL) {
		return -1;
	}

	/* Create IterInstance type dynamically and store in module state */
	state->IterInstanceType = (PyObject *)PyStructSequence_NewType(&iter_instance_desc);
	if (state->IterInstanceType == NULL) {
		return -1;
	}

	/* Create FilesystemIterState type dynamically and store in module state */
	state->FilesystemIterStateType = (PyObject *)PyStructSequence_NewType(&iter_state_desc);
	if (state->FilesystemIterStateType == NULL) {
		return -1;
	}

	/* Initialize FilesystemIterator type */
	if (PyType_Ready(&FilesystemIteratorType) < 0)
		return -1;

	/* Add types to module */
	if (PyModule_AddObjectRef(module, "IterInstance", state->IterInstanceType) < 0) {
		return -1;
	}

	if (PyModule_AddObjectRef(module, "FilesystemIterState", state->FilesystemIterStateType) < 0) {
		return -1;
	}

	return 0;
}
