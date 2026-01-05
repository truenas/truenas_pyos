// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _FHANDLE_H_
#define _FHANDLE_H_

// File handle flags
#ifndef AT_HANDLE_FID
#define AT_HANDLE_FID 0x200
#endif

#ifndef AT_HANDLE_CONNECTABLE
#define AT_HANDLE_CONNECTABLE 0x002
#endif

// Export names for Python module constants
#define FH_AT_SYMLINK_FOLLOW AT_SYMLINK_FOLLOW
#define FH_AT_EMPTY_PATH AT_EMPTY_PATH
#define FH_AT_HANDLE_FID AT_HANDLE_FID
#define FH_AT_HANDLE_CONNECTABLE AT_HANDLE_CONNECTABLE

extern PyTypeObject PyFhandle;

typedef struct {
	PyObject_HEAD
	int mount_id;
	bool is_handle_fd;
	struct file_handle *fhandle;
} py_fhandle_t;

#endif /* _FHANDLE_H_ */
