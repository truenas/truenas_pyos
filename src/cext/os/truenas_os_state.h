// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef TRUENAS_OS_STATE_H
#define TRUENAS_OS_STATE_H

#include <Python.h>

/* Module state - stores PyStructSequence types and enum types */
typedef struct {
	PyObject *StatxResultType;
	PyObject *StatmountResultType;
	PyObject *IdmapMappingEntryType;
	PyObject *CredEntryType;
	PyObject *AccessFailureType;
	PyObject *IterInstanceType;
	PyObject *FilesystemIterStateType;
	PyObject *IteratorRestoreError;
	/* NFS4 ACL enum types */
	PyObject *NFS4AceType_enum;
	PyObject *NFS4Who_enum;
	PyObject *NFS4Perm_enum;
	PyObject *NFS4Flag_enum;
	PyObject *NFS4ACLFlag_enum;
	/* POSIX ACL enum types */
	PyObject *POSIXTag_enum;
	PyObject *POSIXPerm_enum;
	/* truenas_os.Uring op-handle repr strings (see src/cext/uring/) */
	PyObject *uring_repr_openat2;
	PyObject *uring_repr_pread;
	PyObject *uring_repr_pwrite;
	PyObject *uring_repr_close;
	PyObject *uring_repr_statx;
	PyObject *uring_repr_install;
} truenas_os_state_t;

/* Get module state */
truenas_os_state_t *get_truenas_os_state(PyObject *module);

#endif /* TRUENAS_OS_STATE_H */
