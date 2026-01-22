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
#include <sys/xattr.h>
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

/* Forward declarations */
static PyTypeObject FilesystemIteratorType;

/* IterInstance struct sequence indices */
enum {
    ITER_INST_PATH = 0,
    ITER_INST_FD,
    ITER_INST_STATX,
    ITER_INST_ISDIR,
    ITER_INST_NUM_FIELDS
};

/* IterInstance struct sequence type */
static PyStructSequence_Field iter_instance_fields[] = {
    {"path", "Path to the file/directory"},
    {"fd", "Open file descriptor"},
    {"statxinfo", "Statx result object"},
    {"isdir", "True if directory, False if file"},
    {NULL}
};

static PyStructSequence_Desc iter_instance_desc = {
    "IterInstance",
    "Filesystem iteration instance",
    iter_instance_fields,
    ITER_INST_NUM_FIELDS
};

/* FilesystemIterState struct sequence indices */
enum {
    STATE_BTIME_CUTOFF = 0,
    STATE_CNT,
    STATE_CNT_BYTES,
    STATE_RESUME_TOKEN_NAME,
    STATE_RESUME_TOKEN_DATA,
    STATE_FILE_OPEN_FLAGS,
    STATE_NUM_FIELDS
};

static PyStructSequence_Field iter_state_fields[] = {
    {"btime_cutoff", "Birth time cutoff timestamp"},
    {"cnt", "Count of items yielded"},
    {"cnt_bytes", "Total bytes of files yielded"},
    {"resume_token_name", "Resume token xattr name"},
    {"resume_token_data", "Resume token xattr data"},
    {"file_open_flags", "Flags for opening files"},
    {NULL}
};

static PyStructSequence_Desc iter_state_desc = {
    "FilesystemIterState",
    "Filesystem iteration state",
    iter_state_fields,
    STATE_NUM_FIELDS
};

/*
 * Convert Python FilesystemIterState to C iter_state_t
 * Returns 0 on success, -1 on error (with Python exception set)
 */
static int
py_fsiter_state_to_struct(PyObject *state_obj, iter_state_t *c_state)
{
    PyObject *btime_obj, *cnt_obj, *cnt_bytes_obj;
    PyObject *resume_name, *resume_data, *flags_obj;
    const char *token_name, *token_data;
    Py_ssize_t data_len;
    truenas_os_state_t *state;

    state = get_truenas_os_state(NULL);
    if (state == NULL || state->FilesystemIterStateType == NULL) {
        PyErr_SetString(PyExc_SystemError, "FilesystemIterState type not initialized");
        return -1;
    }

    if (!PyObject_TypeCheck(state_obj, (PyTypeObject *)state->FilesystemIterStateType)) {
        PyErr_SetString(PyExc_TypeError, "state must be a FilesystemIterState object");
        return -1;
    }

    /* Extract state values */
    btime_obj = PyStructSequence_GET_ITEM(state_obj, STATE_BTIME_CUTOFF);
    cnt_obj = PyStructSequence_GET_ITEM(state_obj, STATE_CNT);
    cnt_bytes_obj = PyStructSequence_GET_ITEM(state_obj, STATE_CNT_BYTES);
    resume_name = PyStructSequence_GET_ITEM(state_obj, STATE_RESUME_TOKEN_NAME);
    resume_data = PyStructSequence_GET_ITEM(state_obj, STATE_RESUME_TOKEN_DATA);
    flags_obj = PyStructSequence_GET_ITEM(state_obj, STATE_FILE_OPEN_FLAGS);

    c_state->btime_cutoff = PyLong_AsLongLong(btime_obj);
    if (c_state->btime_cutoff == -1 && PyErr_Occurred())
        return -1;

    c_state->cnt = PyLong_AsSize_t(cnt_obj);
    if (c_state->cnt == (size_t)-1 && PyErr_Occurred())
        return -1;

    c_state->cnt_bytes = PyLong_AsSize_t(cnt_bytes_obj);
    if (c_state->cnt_bytes == (size_t)-1 && PyErr_Occurred())
        return -1;

    c_state->file_open_flags = PyLong_AsLong(flags_obj);
    if (c_state->file_open_flags == -1 && PyErr_Occurred())
        return -1;

    /* Handle resume token */
    if (resume_name != Py_None && resume_data != Py_None) {
        token_name = PyUnicode_AsUTF8(resume_name);
        if (token_name == NULL)
            return -1;

        strncpy(c_state->resume_token_name, token_name, NAME_MAX);
        c_state->resume_token_name[NAME_MAX] = '\0';

        if (PyBytes_AsStringAndSize(resume_data, (char **)&token_data, &data_len) < 0)
            return -1;

        if (data_len != RESUME_TOKEN_MAX_LEN) {
            PyErr_Format(PyExc_ValueError,
                        "resume_token_data must be exactly %d bytes, got %zd",
                        RESUME_TOKEN_MAX_LEN, data_len);
            return -1;
        }

        memcpy(c_state->resume_token_data, token_data, RESUME_TOKEN_MAX_LEN);
        c_state->has_resume_token = true;
    } else {
        c_state->has_resume_token = false;
        c_state->resume_token_name[0] = '\0';
    }

    return 0;
}

/*
 * Convert C iter_state_t to Python FilesystemIterState
 * Returns new reference, or NULL on error
 */
static PyObject *
py_fsiter_state_from_struct(const iter_state_t *c_state)
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

    PyObject *btime_obj = PyLong_FromLongLong(c_state->btime_cutoff);
    PyObject *cnt_obj = PyLong_FromSize_t(c_state->cnt);
    PyObject *cnt_bytes_obj = PyLong_FromSize_t(c_state->cnt_bytes);
    PyObject *flags_obj = PyLong_FromLong(c_state->file_open_flags);

    PyObject *resume_name, *resume_data;
    if (c_state->has_resume_token) {
        resume_name = PyUnicode_FromString(c_state->resume_token_name);
        resume_data = PyBytes_FromStringAndSize((const char *)c_state->resume_token_data,
                                                RESUME_TOKEN_MAX_LEN);
    } else {
        resume_name = Py_NewRef(Py_None);
        resume_data = Py_NewRef(Py_None);
    }

    if (!btime_obj || !cnt_obj || !cnt_bytes_obj || !flags_obj || !resume_name || !resume_data) {
        Py_XDECREF(btime_obj);
        Py_XDECREF(cnt_obj);
        Py_XDECREF(cnt_bytes_obj);
        Py_XDECREF(flags_obj);
        Py_XDECREF(resume_name);
        Py_XDECREF(resume_data);
        Py_DECREF(state_obj);
        return NULL;
    }

    PyStructSequence_SET_ITEM(state_obj, STATE_BTIME_CUTOFF, btime_obj);
    PyStructSequence_SET_ITEM(state_obj, STATE_CNT, cnt_obj);
    PyStructSequence_SET_ITEM(state_obj, STATE_CNT_BYTES, cnt_bytes_obj);
    PyStructSequence_SET_ITEM(state_obj, STATE_RESUME_TOKEN_NAME, resume_name);
    PyStructSequence_SET_ITEM(state_obj, STATE_RESUME_TOKEN_DATA, resume_data);
    PyStructSequence_SET_ITEM(state_obj, STATE_FILE_OPEN_FLAGS, flags_obj);

    return state_obj;
}

/*
 * Helper: Create IterInstance from fd, statx, and path
 */
static PyObject *
create_iter_instance(int fd, const struct statx *st, const char *path)
{
    PyObject *inst, *statx_obj, *path_obj, *fd_obj, *isdir_obj;
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
    path_obj = PyUnicode_FromString(path);
    fd_obj = PyLong_FromLong(fd);
    isdir_obj = Py_NewRef(isdir ? Py_True : Py_False);

    if (!path_obj || !fd_obj) {
        Py_XDECREF(path_obj);
        Py_XDECREF(fd_obj);
        Py_DECREF(isdir_obj);
        Py_DECREF(statx_obj);
        Py_DECREF(inst);
        return NULL;
    }

    PyStructSequence_SET_ITEM(inst, ITER_INST_PATH, path_obj);
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
    /* Clean up stack - close any open directories */
    for (size_t i = 0; i < self->cur_depth; i++) {
        if (self->dir_stack[i].dirp) {
            closedir(self->dir_stack[i].dirp);
        }
        if (self->dir_stack[i].fd >= 0) {
            close(self->dir_stack[i].fd);
        }
        free(self->dir_stack[i].path);
    }

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
process_next_entry(FilesystemIteratorObject *self, fsiter_error_t *err)
{
	iter_dir_t *cur_dir;
	struct dirent *entry;
	int fd, ret;
	struct statx st;
	bool is_dir;
	int open_flags;

	if (self->cur_depth == 0) {
		return FSITER_POP_DIR;  /* Stack exhausted */
	}

	cur_dir = &self->dir_stack[self->cur_depth - 1];

	/* Read next entry from current directory */
	errno = 0;
	entry = readdir(cur_dir->dirp);
	if (entry == NULL) {
		if (errno != 0) {
			SET_ERROR_ERRNO(err, "readdir(%s)", cur_dir->path);
			return FSITER_ERROR;
		}
		/* Directory exhausted */
		return FSITER_POP_DIR;
	}

	/* Skip . and .. */
	if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) {
		return FSITER_CONTINUE;
	}

	/* Build full path */
	ret = snprintf(self->last.path, PATH_MAX, "%s/%s", cur_dir->path, entry->d_name);
	if (ret >= PATH_MAX) {
		SET_ERROR(err, "path too long: %s/%s", cur_dir->path, entry->d_name);
		return FSITER_ERROR;
	}

	/* Open entry with openat2 */
	is_dir = (entry->d_type == DT_DIR);
	open_flags = is_dir ? OFLAGS_DIR_ITER : self->state.file_open_flags;

	fd = openat2_impl(cur_dir->fd, entry->d_name, open_flags, RESOLVE_FLAGS_ITER);
	if (fd < 0) {
		/* ELOOP: intermediate component replaced with symlink (shouldn't be possible)
		 * EXDEV: entry is on a different filesystem (crossed mount boundary)
		 * In both cases, prune this branch and continue iteration.
		 */
		if (errno == ELOOP || errno == EXDEV) {
			return FSITER_CONTINUE;
		}
		SET_ERROR_ERRNO(err, "openat2(%s)", self->last.path);
		return FSITER_ERROR;
	}

	/* Call statx on fd */
	ret = statx_impl(fd, "", STATX_FLAGS_ITER, STATX_MASK_ITER, &st);
	if (ret < 0) {
		SET_ERROR_ERRNO(err, "statx(%s)", self->last.path);
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

	/* Check resume token for directories */
	if (S_ISDIR(st.stx_mode) && self->state.has_resume_token) {
		unsigned char xat_bytes[RESUME_TOKEN_MAX_LEN];
		ssize_t xat_len;

		xat_len = fgetxattr(fd, self->state.resume_token_name, xat_bytes, RESUME_TOKEN_MAX_LEN);
		if (xat_len == RESUME_TOKEN_MAX_LEN) {
			/* Check if token matches - directory was already completed */
			if (memcmp(xat_bytes, self->state.resume_token_data, RESUME_TOKEN_MAX_LEN) == 0) {
				close(fd);
				return FSITER_CONTINUE;
			}
		}
		/* If xattr doesn't exist or can't be read, continue normally */
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
push_dir_stack(FilesystemIteratorObject *self, fsiter_error_t *err)
{
	iter_dir_t *new_dir;
	DIR *dirp;
	int dup_fd;

	/* Check depth limit */
	if (self->cur_depth >= MAX_DEPTH) {
		SET_ERROR(err, "max depth %d exceeded at %s", MAX_DEPTH, self->last.path);
		return false;
	}

	/* Duplicate fd since the original will be returned to Python and closed by them */
	dup_fd = dup(self->last.fd);
	if (dup_fd < 0) {
		SET_ERROR_ERRNO(err, "dup(%s)", self->last.path);
		return false;
	}

	/* Open DIR* from duplicated fd - fdopendir takes ownership */
	dirp = fdopendir(dup_fd);
	if (dirp == NULL) {
		SET_ERROR_ERRNO(err, "fdopendir(%s)", self->last.path);
		close(dup_fd);
		return false;
	}

	/* fdopendir succeeded, dirp now owns dup_fd */

	/* Allocate path string */
	new_dir = &self->dir_stack[self->cur_depth];
	new_dir->path = strdup(self->last.path);
	if (new_dir->path == NULL) {
		SET_ERROR(err, "strdup failed for %s", self->last.path);
		closedir(dirp);
		return false;
	}

	new_dir->dirp = dirp;
	new_dir->fd = dirfd(dirp);
	self->cur_depth++;

	return true;
}

/*
 * Pop directory from stack
 * Called with GIL released
 * Handles resume token xattr if configured
 */
static bool
pop_dir_stack(FilesystemIteratorObject *self, fsiter_error_t *err)
{
	iter_dir_t *dir;

	if (self->cur_depth == 0) {
		return true;  /* Nothing to pop */
	}

	dir = &self->dir_stack[self->cur_depth - 1];

	/* Set resume token xattr if configured */
	if (self->state.has_resume_token) {
		/* There's nothing we can really do here if we fail setxtattr */
		fsetxattr(dir->fd, self->state.resume_token_name,
		          self->state.resume_token_data, RESUME_TOKEN_MAX_LEN, 0);
	}

	/* Clean up directory */
	if (dir->dirp) {
		closedir(dir->dirp);
	}
	free(dir->path);

	self->cur_depth--;
	return true;
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

	/* Close fd from previous iteration */
	if (self->last.fd >= 0) {
		close(self->last.fd);
		self->last.fd = -1;
	}

	/* Main iteration loop */
	while (self->cur_depth > 0) {
		/* Process next entry with GIL released */
		Py_BEGIN_ALLOW_THREADS
		action = process_next_entry(self, &self->cerr);
		Py_END_ALLOW_THREADS

		switch (action) {
		case FSITER_ERROR:
			/* Convert error buffer to Python exception */
			PyErr_SetString(PyExc_OSError, self->cerr.message);
			return NULL;

		case FSITER_CONTINUE:
			continue;

		case FSITER_YIELD_FILE:
			/* Create IterInstance for file */
			result = create_iter_instance(self->last.fd, &self->last.st, self->last.path);
			if (result == NULL)
				return NULL;

			/* Update counters */
			self->state.cnt++;
			self->state.cnt_bytes += self->last.st.stx_size;

			return result;

		case FSITER_YIELD_DIR:
			/* Create IterInstance for directory */
			result = create_iter_instance(self->last.fd, &self->last.st, self->last.path);
			if (result == NULL)
				return NULL;

			/* Push directory onto stack with GIL released */
			Py_BEGIN_ALLOW_THREADS
			push_ok = push_dir_stack(self, &self->cerr);
			Py_END_ALLOW_THREADS

			if (!push_ok) {
				Py_DECREF(result);
				PyErr_SetString(PyExc_OSError, self->cerr.message);
				return NULL;
			}

			/* Update counter */
			self->state.cnt++;

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
"  - btime_cutoff: Birth time cutoff value\n"
"  - cnt: Number of items yielded so far\n"
"  - cnt_bytes: Total bytes of files yielded\n"
"  - resume_token_name: Resume token xattr name (or None)\n"
"  - resume_token_data: Resume token xattr data (or None)\n"
"  - file_open_flags: Flags used for opening files\n"
);

static PyObject *
FilesystemIterator_get_stats(FilesystemIteratorObject *self, PyObject *Py_UNUSED(ignored))
{
	return py_fsiter_state_from_struct(&self->state);
}

/* FilesystemIterator methods */
static PyMethodDef FilesystemIterator_methods[] = {
	{
		.ml_name = "get_stats",
		.ml_meth = (PyCFunction)FilesystemIterator_get_stats,
		.ml_flags = METH_NOARGS,
		.ml_doc = FilesystemIterator_get_stats__doc__
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
                           const char *filesystem_name, const iter_state_t *state)
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

	/* Build root path */
	if (relative_path && relative_path[0] != '\0') {
		snprintf(root_path, sizeof(root_path), "%s/%s", mountpoint, relative_path);
	} else {
		snprintf(root_path, sizeof(root_path), "%s", mountpoint);
	}

	/* Open root directory with openat2 */
	root_fd = openat2_impl(AT_FDCWD, root_path, OFLAGS_DIR_ITER, RESOLVE_NO_SYMLINKS);
	if (root_fd < 0) {
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, root_path);
		return NULL;
	}

	/* Call statx on root to validate mount */
	ret = statx_impl(root_fd, "", STATX_FLAGS_ITER, STATX_MASK_ITER, &root_st);
	if (ret < 0) {
		close(root_fd);
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, root_path);
		return NULL;
	}

	/* Validate it's a directory */
	if (!S_ISDIR(root_st.stx_mode)) {
		close(root_fd);
		PyErr_Format(PyExc_NotADirectoryError, "Not a directory: %s", root_path);
		return NULL;
	}

#ifdef STATMOUNT_SB_SOURCE
	/* Validate mount source using statmount */
	sm = statmount_impl(root_st.stx_mnt_id, STATMOUNT_SB_BASIC | STATMOUNT_SB_SOURCE);
	if (sm == NULL) {
		close(root_fd);
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, root_path);
		return NULL;
	}

	sb_source = sm->str + sm->sb_source;
	if (strcmp(sb_source, filesystem_name) != 0) {
		PyMem_RawFree(sm);
		close(root_fd);
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
		PyErr_SetFromErrnoWithFilename(PyExc_OSError, root_path);
		return NULL;
	}

	/* Create iterator object */
	iter = PyObject_New(FilesystemIteratorObject, &FilesystemIteratorType);
	if (iter == NULL) {
		closedir(root_dirp);
		return NULL;
	}

	/* Initialize iterator fields */
	memset(iter->dir_stack, 0, sizeof(iter->dir_stack));
	memset(&iter->last, 0, sizeof(iter->last));
	iter->last.fd = -1;
	iter->cur_depth = 0;

	/* Copy state into iterator */
	memcpy(&iter->state, state, sizeof(iter_state_t));

	/* Initialize stack with root directory */
	root_dir = &iter->dir_stack[0];
	root_dir->path = strdup(root_path);
	if (root_dir->path == NULL) {
		closedir(root_dirp);
		Py_DECREF(iter);
		return PyErr_NoMemory();
	}

	root_dir->dirp = root_dirp;
	root_dir->fd = dirfd(root_dirp);
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
