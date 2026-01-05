// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "mount.h"
#include <linux/mount.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <string.h>

#define __NR_statmount 457
#define __NR_listmount 458

// Define newer STATMOUNT_* constants if not in kernel headers
#ifndef STATMOUNT_FS_SUBTYPE
#define STATMOUNT_FS_SUBTYPE		0x00000100U
#endif
#ifndef STATMOUNT_SB_SOURCE
#define STATMOUNT_SB_SOURCE		0x00000200U
#endif
#ifndef STATMOUNT_OPT_ARRAY
#define STATMOUNT_OPT_ARRAY		0x00000400U
#endif
#ifndef STATMOUNT_OPT_SEC_ARRAY
#define STATMOUNT_OPT_SEC_ARRAY		0x00000800U
#endif
#ifndef STATMOUNT_SUPPORTED_MASK
#define STATMOUNT_SUPPORTED_MASK	0x00001000U
#endif
#ifndef STATMOUNT_MNT_UIDMAP
#define STATMOUNT_MNT_UIDMAP		0x00002000U
#endif
#ifndef STATMOUNT_MNT_GIDMAP
#define STATMOUNT_MNT_GIDMAP		0x00004000U
#endif

// StatmountResult structured sequence type
static PyStructSequence_Field statmount_result_fields[] = {
	{"mnt_id", "Unique ID of the mount (since Linux 3.15)"},
	{"mnt_parent_id", "Unique ID of the parent mount"},
	{"mnt_id_old", "Old mount ID used in /proc/self/mountinfo"},
	{"mnt_parent_id_old", "Old parent mount ID"},
	{"mnt_root", "Pathname of the root of the mount relative to the root of the filesystem"},
	{"mnt_point", "Pathname of the mount point relative to the process's root"},
	{"mnt_attr", "Mount attributes (MOUNT_ATTR_* flags)"},
	{"mnt_propagation", "Mount propagation type (MS_SHARED, MS_SLAVE, MS_PRIVATE, or MS_UNBINDABLE)"},
	{"mnt_peer_group", "ID of the shared peer group for this mount (non-zero if MS_SHARED)"},
	{"mnt_master", "ID of the master mount for this mount (non-zero if MS_SLAVE)"},
	{"propagate_from", "ID of the mount from which propagation occurs in the current namespace"},
	{"fs_type", "Filesystem type (e.g., 'ext4', 'tmpfs')"},
	{"mnt_ns_id", "ID of the mount namespace containing this mount"},
	{"mnt_opts", "Mount options string"},
	{"sb_dev_major", "Major device number of the filesystem's superblock"},
	{"sb_dev_minor", "Minor device number of the filesystem's superblock"},
	{"sb_magic", "Filesystem type magic number"},
	{"sb_flags", "Superblock flags (SB_* constants)"},
	{"fs_subtype", "Filesystem subtype (e.g., 'btrfs' subvolume name)"},
	{"sb_source", "Source string of the mount (block device, network share, etc.)"},
	{"opt_array", "List of filesystem-specific mount options"},
	{"opt_sec_array", "List of security-related mount options (e.g., SELinux context)"},
	{"supported_mask", "Mask of STATMOUNT_* flags supported by this kernel"},
	{"mnt_uidmap", "UID mapping information (for user namespaces)"},
	{"mnt_gidmap", "GID mapping information (for user namespaces)"},
	{"mask", "Mask indicating which fields were requested and returned"},
	{NULL}
};

static PyStructSequence_Desc statmount_result_desc = {
	.name = "truenas_os.StatmountResult",
	.doc = "Result from statmount() system call\n\n"
	       "A named tuple containing information about a mount point. "
	       "Fields that were not requested or are unavailable will be None.",
	.fields = statmount_result_fields,
	.n_in_sequence = 26
};

static PyTypeObject StatmountResultType;

PyObject *do_listmount(uint64_t mnt_id, uint64_t last_mnt_id, int reverse)
{
	struct mnt_id_req req = {0};
	uint64_t mnt_ids[LISTMOUNT_BATCH_SIZE];
	ssize_t count;
	PyObject *result = NULL;
	PyObject *item = NULL;

	result = PyList_New(0);
	if (result == NULL) {
		return NULL;
	}

	req.size = MNT_ID_REQ_SIZE_VER1;
	req.mnt_id = mnt_id;
	req.param = last_mnt_id;
	if (reverse) {
		req.param |= LISTMOUNT_REVERSE;
	}

	// Loop until we get all mount IDs
	while (1) {
		Py_BEGIN_ALLOW_THREADS
		count = syscall(__NR_listmount, &req, mnt_ids, LISTMOUNT_BATCH_SIZE, 0);
		Py_END_ALLOW_THREADS

		if (count < 0) {
			Py_DECREF(result);
			PyErr_SetFromErrno(PyExc_OSError);
			return NULL;
		}

		if (count == 0) {
			break;
		}

		// Add the mount IDs to the list
		for (ssize_t i = 0; i < count; i++) {
			item = PyLong_FromUnsignedLongLong(mnt_ids[i]);
			if (item == NULL) {
				Py_DECREF(result);
				return NULL;
			}
			if (PyList_Append(result, item) < 0) {
				Py_DECREF(item);
				Py_DECREF(result);
				return NULL;
			}
			Py_DECREF(item);
		}

		// If we got fewer than the batch size, we're done
		if (count < LISTMOUNT_BATCH_SIZE) {
			break;
		}

		// Set last_mnt_id for next iteration
		req.param = mnt_ids[count - 1];
		if (reverse) {
			req.param |= LISTMOUNT_REVERSE;
		}
	}

	return result;
}

PyObject *do_statmount(uint64_t mnt_id, uint64_t mask)
{
	struct mnt_id_req req = {0};
	char stack_buf[1024];
	struct statmount *sm = (struct statmount *)stack_buf;
	char *dynamic_buf = NULL;
	size_t buf_size = sizeof(stack_buf);
	ssize_t ret;
	PyObject *result = NULL;

	req.size = MNT_ID_REQ_SIZE_VER1;
	req.mnt_id = mnt_id;
	req.param = mask;

	// Try with stack buffer first
	Py_BEGIN_ALLOW_THREADS
	ret = syscall(__NR_statmount, &req, sm, buf_size, 0);
	Py_END_ALLOW_THREADS

	// If buffer too small, allocate larger buffer in 4KB increments
	while (ret < 0 && errno == EOVERFLOW) {
		buf_size += 4096;
		dynamic_buf = PyMem_RawRealloc(dynamic_buf, buf_size);
		if (dynamic_buf == NULL) {
			PyErr_NoMemory();
			return NULL;
		}
		sm = (struct statmount *)dynamic_buf;

		Py_BEGIN_ALLOW_THREADS
		ret = syscall(__NR_statmount, &req, sm, buf_size, 0);
		Py_END_ALLOW_THREADS
	}

	if (ret < 0) {
		PyMem_RawFree(dynamic_buf);
		PyErr_SetFromErrno(PyExc_OSError);
		return NULL;
	}

	result = PyStructSequence_New(&StatmountResultType);
	if (result == NULL) {
		PyMem_RawFree(dynamic_buf);
		return NULL;
	}

	// Set fields based on what was returned
	PyObject *tmp = NULL;

	// mnt_id, mnt_parent_id, mnt_id_old, mnt_parent_id_old
	if (sm->mask & STATMOUNT_MNT_BASIC) {
		tmp = PyLong_FromUnsignedLongLong(sm->mnt_id);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 0, tmp);

		tmp = PyLong_FromUnsignedLongLong(sm->mnt_parent_id);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 1, tmp);

		tmp = PyLong_FromUnsignedLong(sm->mnt_id_old);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 2, tmp);

		tmp = PyLong_FromUnsignedLong(sm->mnt_parent_id_old);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 3, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 0, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 1, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 2, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 3, Py_NewRef(Py_None));
	}

	// mnt_root, mnt_point
	if (sm->mask & STATMOUNT_MNT_ROOT) {
		tmp = sm->mnt_root ? PyUnicode_FromString(sm->str + sm->mnt_root) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 4, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 4, Py_NewRef(Py_None));
	}

	if (sm->mask & STATMOUNT_MNT_POINT) {
		tmp = sm->mnt_point ? PyUnicode_FromString(sm->str + sm->mnt_point) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 5, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 5, Py_NewRef(Py_None));
	}

	// mnt_attr, mnt_propagation, mnt_peer_group, mnt_master
	if (sm->mask & STATMOUNT_MNT_BASIC) {
		tmp = PyLong_FromUnsignedLongLong(sm->mnt_attr);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 6, tmp);

		tmp = PyLong_FromUnsignedLongLong(sm->mnt_propagation);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 7, tmp);

		tmp = PyLong_FromUnsignedLongLong(sm->mnt_peer_group);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 8, tmp);

		tmp = PyLong_FromUnsignedLongLong(sm->mnt_master);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 9, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 6, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 7, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 8, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 9, Py_NewRef(Py_None));
	}

	// propagate_from
	if (sm->mask & STATMOUNT_PROPAGATE_FROM) {
		tmp = PyLong_FromUnsignedLongLong(sm->propagate_from);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 10, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 10, Py_NewRef(Py_None));
	}

	// fs_type
	if (sm->mask & STATMOUNT_FS_TYPE) {
		tmp = sm->fs_type ? PyUnicode_FromString(sm->str + sm->fs_type) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 11, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 11, Py_NewRef(Py_None));
	}

	// mnt_ns_id
	if (sm->mask & STATMOUNT_MNT_NS_ID) {
		tmp = PyLong_FromUnsignedLongLong(sm->mnt_ns_id);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 12, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 12, Py_NewRef(Py_None));
	}

	// mnt_opts
	if (sm->mask & STATMOUNT_MNT_OPTS) {
		tmp = sm->mnt_opts ? PyUnicode_FromString(sm->str + sm->mnt_opts) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 13, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 13, Py_NewRef(Py_None));
	}

	// sb_dev_major, sb_dev_minor, sb_magic, sb_flags
	if (sm->mask & STATMOUNT_SB_BASIC) {
		tmp = PyLong_FromUnsignedLong(sm->sb_dev_major);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 14, tmp);

		tmp = PyLong_FromUnsignedLong(sm->sb_dev_minor);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 15, tmp);

		tmp = PyLong_FromUnsignedLongLong(sm->sb_magic);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 16, tmp);

		tmp = PyLong_FromUnsignedLong(sm->sb_flags);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 17, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 14, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 15, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 16, Py_NewRef(Py_None));
		PyStructSequence_SET_ITEM(result, 17, Py_NewRef(Py_None));
	}

	// fs_subtype
	if (sm->mask & STATMOUNT_FS_SUBTYPE) {
		tmp = sm->fs_subtype ? PyUnicode_FromString(sm->str + sm->fs_subtype) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 18, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 18, Py_NewRef(Py_None));
	}

	// sb_source
	if (sm->mask & STATMOUNT_SB_SOURCE) {
		tmp = sm->sb_source ? PyUnicode_FromString(sm->str + sm->sb_source) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 19, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 19, Py_NewRef(Py_None));
	}

	// opt_array (array of null-terminated strings)
	if (sm->mask & STATMOUNT_OPT_ARRAY) {
		if (sm->opt_array && sm->opt_num > 0) {
			PyObject *opt_list = PyList_New(0);
			if (opt_list == NULL) {
				Py_DECREF(result);
				result = NULL;
				goto cleanup;
			}
			const char *opt_ptr = sm->str + sm->opt_array;
			for (uint32_t i = 0; i < sm->opt_num; i++) {
				PyObject *opt_str = PyUnicode_FromString(opt_ptr);
				if (opt_str == NULL) {
					Py_DECREF(opt_list);
					Py_DECREF(result);
					result = NULL;
					goto cleanup;
				}
				if (PyList_Append(opt_list, opt_str) < 0) {
					Py_DECREF(opt_str);
					Py_DECREF(opt_list);
					Py_DECREF(result);
					result = NULL;
					goto cleanup;
				}
				Py_DECREF(opt_str);
				opt_ptr += strlen(opt_ptr) + 1;
			}
			PyStructSequence_SET_ITEM(result, 20, opt_list);
		} else {
			PyStructSequence_SET_ITEM(result, 20, Py_NewRef(Py_None));
		}
	} else {
		PyStructSequence_SET_ITEM(result, 20, Py_NewRef(Py_None));
	}

	// opt_sec_array (array of null-terminated security options)
	if (sm->mask & STATMOUNT_OPT_SEC_ARRAY) {
		if (sm->opt_sec_array && sm->opt_sec_num > 0) {
			PyObject *opt_sec_list = PyList_New(0);
			if (opt_sec_list == NULL) {
				Py_DECREF(result);
				result = NULL;
				goto cleanup;
			}
			const char *opt_ptr = sm->str + sm->opt_sec_array;
			for (uint32_t i = 0; i < sm->opt_sec_num; i++) {
				PyObject *opt_str = PyUnicode_FromString(opt_ptr);
				if (opt_str == NULL) {
					Py_DECREF(opt_sec_list);
					Py_DECREF(result);
					result = NULL;
					goto cleanup;
				}
				if (PyList_Append(opt_sec_list, opt_str) < 0) {
					Py_DECREF(opt_str);
					Py_DECREF(opt_sec_list);
					Py_DECREF(result);
					result = NULL;
					goto cleanup;
				}
				Py_DECREF(opt_str);
				opt_ptr += strlen(opt_ptr) + 1;
			}
			PyStructSequence_SET_ITEM(result, 21, opt_sec_list);
		} else {
			PyStructSequence_SET_ITEM(result, 21, Py_NewRef(Py_None));
		}
	} else {
		PyStructSequence_SET_ITEM(result, 21, Py_NewRef(Py_None));
	}

	// supported_mask
	if (sm->mask & STATMOUNT_SUPPORTED_MASK) {
		tmp = PyLong_FromUnsignedLongLong(sm->supported_mask);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 22, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 22, Py_NewRef(Py_None));
	}

	// mnt_uidmap
	if (sm->mask & STATMOUNT_MNT_UIDMAP) {
		tmp = sm->mnt_uidmap ? PyUnicode_FromString(sm->str + sm->mnt_uidmap) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 23, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 23, Py_NewRef(Py_None));
	}

	// mnt_gidmap
	if (sm->mask & STATMOUNT_MNT_GIDMAP) {
		tmp = sm->mnt_gidmap ? PyUnicode_FromString(sm->str + sm->mnt_gidmap) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, 24, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, 24, Py_NewRef(Py_None));
	}

	// mask
	tmp = PyLong_FromUnsignedLongLong(sm->mask);
	if (tmp == NULL) {
		Py_DECREF(result);
		result = NULL;
		goto cleanup;
	}
	PyStructSequence_SET_ITEM(result, 25, tmp);

cleanup:
	PyMem_RawFree(dynamic_buf);
	return result;
}

int init_mount_types(PyObject *module)
{
	if (PyStructSequence_InitType2(&StatmountResultType, &statmount_result_desc) < 0) {
		return -1;
	}

	Py_INCREF(&StatmountResultType);
	if (PyModule_AddObject(module, "StatmountResult", (PyObject *)&StatmountResultType) < 0) {
		Py_DECREF(&StatmountResultType);
		return -1;
	}

	// Add STATMOUNT_* constants
	PyModule_AddIntConstant(module, "STATMOUNT_SB_BASIC", STATMOUNT_SB_BASIC);
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_BASIC", STATMOUNT_MNT_BASIC);
	PyModule_AddIntConstant(module, "STATMOUNT_PROPAGATE_FROM", STATMOUNT_PROPAGATE_FROM);
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_ROOT", STATMOUNT_MNT_ROOT);
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_POINT", STATMOUNT_MNT_POINT);
	PyModule_AddIntConstant(module, "STATMOUNT_FS_TYPE", STATMOUNT_FS_TYPE);
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_NS_ID", STATMOUNT_MNT_NS_ID);
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_OPTS", STATMOUNT_MNT_OPTS);
	PyModule_AddIntConstant(module, "STATMOUNT_FS_SUBTYPE", STATMOUNT_FS_SUBTYPE);
	PyModule_AddIntConstant(module, "STATMOUNT_SB_SOURCE", STATMOUNT_SB_SOURCE);
	PyModule_AddIntConstant(module, "STATMOUNT_OPT_ARRAY", STATMOUNT_OPT_ARRAY);
	PyModule_AddIntConstant(module, "STATMOUNT_OPT_SEC_ARRAY", STATMOUNT_OPT_SEC_ARRAY);
	PyModule_AddIntConstant(module, "STATMOUNT_SUPPORTED_MASK", STATMOUNT_SUPPORTED_MASK);
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_UIDMAP", STATMOUNT_MNT_UIDMAP);
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_GIDMAP", STATMOUNT_MNT_GIDMAP);

	// Add STATMOUNT_ALL convenience constant (common flags, excludes UIDMAP/GIDMAP)
	PyModule_AddIntConstant(module, "STATMOUNT_ALL",
		STATMOUNT_SB_BASIC | STATMOUNT_MNT_BASIC | STATMOUNT_PROPAGATE_FROM |
		STATMOUNT_MNT_ROOT | STATMOUNT_MNT_POINT | STATMOUNT_FS_TYPE |
		STATMOUNT_MNT_NS_ID | STATMOUNT_MNT_OPTS | STATMOUNT_FS_SUBTYPE |
		STATMOUNT_SB_SOURCE | STATMOUNT_OPT_ARRAY | STATMOUNT_OPT_SEC_ARRAY |
		STATMOUNT_SUPPORTED_MASK);

	// Add MOUNT_ATTR_* constants
	PyModule_AddIntConstant(module, "MOUNT_ATTR_RDONLY", MOUNT_ATTR_RDONLY);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_NOSUID", MOUNT_ATTR_NOSUID);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_NODEV", MOUNT_ATTR_NODEV);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_NOEXEC", MOUNT_ATTR_NOEXEC);
	PyModule_AddIntConstant(module, "MOUNT_ATTR__ATIME", MOUNT_ATTR__ATIME);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_RELATIME", MOUNT_ATTR_RELATIME);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_NOATIME", MOUNT_ATTR_NOATIME);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_STRICTATIME", MOUNT_ATTR_STRICTATIME);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_NODIRATIME", MOUNT_ATTR_NODIRATIME);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_IDMAP", MOUNT_ATTR_IDMAP);
	PyModule_AddIntConstant(module, "MOUNT_ATTR_NOSYMFOLLOW", MOUNT_ATTR_NOSYMFOLLOW);

	return 0;
}
