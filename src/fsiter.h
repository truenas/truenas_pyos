#ifndef TRUENAS_FSITER_H
#define TRUENAS_FSITER_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <dirent.h>
#include <sys/types.h>
#include <limits.h>
#include <stdbool.h>

/* Maximum directory depth for stack allocation */
#define MAX_DEPTH 2048

/* UUID byte length for resume tokens */
#define RESUME_TOKEN_MAX_LEN 16

/* Error buffer for operations without GIL */
typedef struct {
	char message[8192];
} fsiter_error_t;

typedef struct {
	char name[NAME_MAX + 1];	/* Directory entry name (not full path) */
	struct statx st;
	int fd;
	bool is_dir;
} iter_entry_t;

/* Directory stack entry */
typedef struct {
	char *path;		/* Current path string (allocated) */
	DIR *dirp;		/* DIR pointer from fdopendir */
} iter_dir_t;

/* Iteration state parameters */
typedef struct {
	long long btime_cutoff;
	size_t cnt;
	size_t cnt_bytes;
	char resume_token_name[NAME_MAX + 1];      /* +1 for null terminator */
	unsigned char resume_token_data[RESUME_TOKEN_MAX_LEN];
	int file_open_flags;
	bool has_resume_token;                      /* Flag: resume token enabled */
} iter_state_t;

/* Iterator object returned by iter_filesystem_contents */
typedef struct {
	PyObject_HEAD

	iter_dir_t dir_stack[MAX_DEPTH];    /* Pre-allocated stack */
	size_t cur_depth;                   /* Current stack depth */
	iter_state_t state;                 /* Iteration state and configuration */
	iter_entry_t last;
	fsiter_error_t cerr;

	size_t reporting_cb_increment;
	PyObject *reporting_cb;
	PyObject *reporting_cb_private_data;
	bool skip_next_recursion;           /* Flag: skip recursion into last dir */
} FilesystemIteratorObject;

/* Module initialization function - initializes all types */
int init_iter_types(PyObject *module);

/* Create iterator object - to be called from truenas_pyos.c */
PyObject* create_filesystem_iterator(const char *mountpoint, const char *relative_path,
				     const char *filesystem_name, const iter_state_t *state,
				     size_t reporting_cb_increment,
				     PyObject *reporting_cb,
				     PyObject *reporting_cb_private_data);

#endif /* TRUENAS_FSITER_H */
