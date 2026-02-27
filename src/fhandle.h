// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _FHANDLE_H_
#define _FHANDLE_H_

#include <stdint.h>

// File handle flags
#ifndef AT_HANDLE_FID
#define AT_HANDLE_FID 0x200
#endif

#ifndef AT_HANDLE_CONNECTABLE
#define AT_HANDLE_CONNECTABLE 0x002
#endif

#ifndef AT_HANDLE_MNT_ID_UNIQUE
// Return unique 64-bit mount ID in *mount_id (stored as int, low 32 bits).
// Compatible with statmount(2) and STATX_MNT_ID_UNIQUE (since Linux 6.5).
#define AT_HANDLE_MNT_ID_UNIQUE 0x001
#endif

// Export names for Python module constants
#define FH_AT_SYMLINK_FOLLOW AT_SYMLINK_FOLLOW
#define FH_AT_EMPTY_PATH AT_EMPTY_PATH
#define FH_AT_HANDLE_FID AT_HANDLE_FID
#define FH_AT_HANDLE_CONNECTABLE AT_HANDLE_CONNECTABLE
#define FH_AT_HANDLE_MNT_ID_UNIQUE AT_HANDLE_MNT_ID_UNIQUE

extern PyTypeObject PyFhandle;

typedef struct {
	PyObject_HEAD
	// mount_id is either the legacy 32-bit mount ID (from name_to_handle_at
	// without AT_HANDLE_MNT_ID_UNIQUE) or the full 64-bit unique mount ID
	// (obtained via statx(STATX_MNT_ID_UNIQUE) after the handle call).
	uint64_t mount_id;
	bool is_handle_fd;
	// True when mount_id is the unique 64-bit value (compatible with
	// statmount(2) and STATX_MNT_ID_UNIQUE).
	bool unique_mount_id;
	char fhbuf[MAX_HANDLE_SZ + offsetof(struct file_handle, f_handle)];
	struct file_handle *fhandle;
} py_fhandle_t;

#endif /* _FHANDLE_H_ */
