// SPDX-License-Identifier: LGPL-3.0-or-later

#include <Python.h>
#include "common/includes.h"
#include "mount.h"
#include "truenas_os_state.h"
#include <linux/mount.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <string.h>

#define __NR_statmount 457
#define __NR_listmount 458

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
#ifdef STATMOUNT_FS_SUBTYPE
	{"fs_subtype", "Filesystem subtype (e.g., 'btrfs' subvolume name)"},
#endif
#ifdef STATMOUNT_SB_SOURCE
	{"sb_source", "Source string of the mount (block device, network share, etc.)"},
#endif
#ifdef STATMOUNT_OPT_ARRAY
	{"opt_array", "List of filesystem-specific mount options"},
#endif
#ifdef STATMOUNT_OPT_SEC_ARRAY
	{"opt_sec_array", "List of security-related mount options (e.g., SELinux context)"},
#endif
#ifdef STATMOUNT_SUPPORTED_MASK
	{"supported_mask", "Mask of STATMOUNT_* flags supported by this kernel"},
#endif
#ifdef STATMOUNT_MNT_UIDMAP
	{"mnt_uidmap", "UID mapping information (for user namespaces)"},
#endif
#ifdef STATMOUNT_MNT_GIDMAP
	{"mnt_gidmap", "GID mapping information (for user namespaces)"},
#endif
	{"mask", "Mask indicating which fields were requested and returned"},
	{NULL}
};

static PyStructSequence_Desc statmount_result_desc = {
	.name = "truenas_os.StatmountResult",
	.doc = "Result from statmount() system call\n\n"
	       "A named tuple containing information about a mount point. "
	       "Fields that were not requested or are unavailable will be None.",
	.fields = statmount_result_fields,
	.n_in_sequence = 19  // Base fields
#ifdef STATMOUNT_FS_SUBTYPE
		+ 1
#endif
#ifdef STATMOUNT_SB_SOURCE
		+ 1
#endif
#ifdef STATMOUNT_OPT_ARRAY
		+ 1
#endif
#ifdef STATMOUNT_OPT_SEC_ARRAY
		+ 1
#endif
#ifdef STATMOUNT_SUPPORTED_MASK
		+ 1
#endif
#ifdef STATMOUNT_MNT_UIDMAP
		+ 1
#endif
#ifdef STATMOUNT_MNT_GIDMAP
		+ 1
#endif
};

// Calculate field indices dynamically based on what's compiled in
#define IDX_MASK (18)  // Always last base field
#ifdef STATMOUNT_FS_SUBTYPE
#define IDX_FS_SUBTYPE (18)
#define IDX_BASE_NEXT (19)
#else
#define IDX_BASE_NEXT (18)
#endif

#ifdef STATMOUNT_SB_SOURCE
#define IDX_SB_SOURCE (IDX_BASE_NEXT)
#define IDX_SB_SOURCE_NEXT (IDX_BASE_NEXT + 1)
#else
#define IDX_SB_SOURCE_NEXT (IDX_BASE_NEXT)
#endif

#ifdef STATMOUNT_OPT_ARRAY
#define IDX_OPT_ARRAY (IDX_SB_SOURCE_NEXT)
#define IDX_OPT_ARRAY_NEXT (IDX_SB_SOURCE_NEXT + 1)
#else
#define IDX_OPT_ARRAY_NEXT (IDX_SB_SOURCE_NEXT)
#endif

#ifdef STATMOUNT_OPT_SEC_ARRAY
#define IDX_OPT_SEC_ARRAY (IDX_OPT_ARRAY_NEXT)
#define IDX_OPT_SEC_ARRAY_NEXT (IDX_OPT_ARRAY_NEXT + 1)
#else
#define IDX_OPT_SEC_ARRAY_NEXT (IDX_OPT_ARRAY_NEXT)
#endif

#ifdef STATMOUNT_SUPPORTED_MASK
#define IDX_SUPPORTED_MASK (IDX_OPT_SEC_ARRAY_NEXT)
#define IDX_SUPPORTED_MASK_NEXT (IDX_OPT_SEC_ARRAY_NEXT + 1)
#else
#define IDX_SUPPORTED_MASK_NEXT (IDX_OPT_SEC_ARRAY_NEXT)
#endif

#ifdef STATMOUNT_MNT_UIDMAP
#define IDX_MNT_UIDMAP (IDX_SUPPORTED_MASK_NEXT)
#define IDX_MNT_UIDMAP_NEXT (IDX_SUPPORTED_MASK_NEXT + 1)
#else
#define IDX_MNT_UIDMAP_NEXT (IDX_SUPPORTED_MASK_NEXT)
#endif

#ifdef STATMOUNT_MNT_GIDMAP
#define IDX_MNT_GIDMAP (IDX_MNT_UIDMAP_NEXT)
#define IDX_MNT_GIDMAP_NEXT (IDX_MNT_UIDMAP_NEXT + 1)
#else
#define IDX_MNT_GIDMAP_NEXT (IDX_MNT_UIDMAP_NEXT)
#endif

#define IDX_MASK_FINAL (IDX_MNT_GIDMAP_NEXT)

PyObject *do_listmount(uint64_t mnt_id, uint64_t last_mnt_id, int reverse)
{
	struct mnt_id_req req = {0};
	uint64_t mnt_ids[LISTMOUNT_BATCH_SIZE];
	ssize_t count;
	PyObject *result = NULL;
	PyObject *item = NULL;
	unsigned long flags = reverse ? LISTMOUNT_REVERSE : 0;

	result = PyList_New(0);
	if (result == NULL) {
		return NULL;
	}

	req.size = MNT_ID_REQ_SIZE_VER1;
	req.mnt_id = mnt_id;
	req.param = last_mnt_id;

	// Loop until we get all mount IDs
	while (1) {
		Py_BEGIN_ALLOW_THREADS
		count = syscall(__NR_listmount, &req, mnt_ids, LISTMOUNT_BATCH_SIZE, flags);
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
	truenas_os_state_t *state = NULL;

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

	state = get_truenas_os_state(NULL);
	if (state == NULL || state->StatmountResultType == NULL) {
		PyMem_RawFree(dynamic_buf);
		PyErr_SetString(PyExc_SystemError, "StatmountResult type not initialized");
		return NULL;
	}

	result = PyStructSequence_New((PyTypeObject *)state->StatmountResultType);
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

#ifdef STATMOUNT_FS_SUBTYPE
	// fs_subtype
	if (sm->mask & STATMOUNT_FS_SUBTYPE) {
		tmp = sm->fs_subtype ? PyUnicode_FromString(sm->str + sm->fs_subtype) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, IDX_FS_SUBTYPE, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, IDX_FS_SUBTYPE, Py_NewRef(Py_None));
	}
#endif

#ifdef STATMOUNT_SB_SOURCE
	// sb_source
	if (sm->mask & STATMOUNT_SB_SOURCE) {
		tmp = sm->sb_source ? PyUnicode_FromString(sm->str + sm->sb_source) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, IDX_SB_SOURCE, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, IDX_SB_SOURCE, Py_NewRef(Py_None));
	}
#endif

#ifdef STATMOUNT_OPT_ARRAY
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
			PyStructSequence_SET_ITEM(result, IDX_OPT_ARRAY, opt_list);
		} else {
			PyStructSequence_SET_ITEM(result, IDX_OPT_ARRAY, Py_NewRef(Py_None));
		}
	} else {
		PyStructSequence_SET_ITEM(result, IDX_OPT_ARRAY, Py_NewRef(Py_None));
	}
#endif

#ifdef STATMOUNT_OPT_SEC_ARRAY
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
			PyStructSequence_SET_ITEM(result, IDX_OPT_SEC_ARRAY, opt_sec_list);
		} else {
			PyStructSequence_SET_ITEM(result, IDX_OPT_SEC_ARRAY, Py_NewRef(Py_None));
		}
	} else {
		PyStructSequence_SET_ITEM(result, IDX_OPT_SEC_ARRAY, Py_NewRef(Py_None));
	}
#endif

#ifdef STATMOUNT_SUPPORTED_MASK
	// supported_mask
	if (sm->mask & STATMOUNT_SUPPORTED_MASK) {
		tmp = PyLong_FromUnsignedLongLong(sm->supported_mask);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, IDX_SUPPORTED_MASK, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, IDX_SUPPORTED_MASK, Py_NewRef(Py_None));
	}
#endif

#ifdef STATMOUNT_MNT_UIDMAP
	// mnt_uidmap
	if (sm->mask & STATMOUNT_MNT_UIDMAP) {
		tmp = sm->mnt_uidmap ? PyUnicode_FromString(sm->str + sm->mnt_uidmap) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, IDX_MNT_UIDMAP, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, IDX_MNT_UIDMAP, Py_NewRef(Py_None));
	}
#endif

#ifdef STATMOUNT_MNT_GIDMAP
	// mnt_gidmap
	if (sm->mask & STATMOUNT_MNT_GIDMAP) {
		tmp = sm->mnt_gidmap ? PyUnicode_FromString(sm->str + sm->mnt_gidmap) : Py_NewRef(Py_None);
		if (tmp == NULL) {
			Py_DECREF(result);
			result = NULL;
			goto cleanup;
		}
		PyStructSequence_SET_ITEM(result, IDX_MNT_GIDMAP, tmp);
	} else {
		PyStructSequence_SET_ITEM(result, IDX_MNT_GIDMAP, Py_NewRef(Py_None));
	}
#endif

	// mask
	tmp = PyLong_FromUnsignedLongLong(sm->mask);
	if (tmp == NULL) {
		Py_DECREF(result);
		result = NULL;
		goto cleanup;
	}
	PyStructSequence_SET_ITEM(result, IDX_MASK_FINAL, tmp);

cleanup:
	PyMem_RawFree(dynamic_buf);
	return result;
}

/*
 * C wrapper for statmount() - returns pointer to statmount struct
 * Caller must free the returned pointer with PyMem_RawFree()
 * Returns NULL with errno set on error
 */
struct statmount *statmount_impl(uint64_t mnt_id, uint64_t mask)
{
	struct mnt_id_req req = {0};
	struct statmount *sm = NULL;
	size_t buf_size = 4096;
	ssize_t ret;

	req.size = MNT_ID_REQ_SIZE_VER1;
	req.mnt_id = mnt_id;
	req.param = mask;

	sm = PyMem_RawMalloc(buf_size);
	if (sm == NULL) {
		errno = ENOMEM;
		return NULL;
	}

	ret = syscall(__NR_statmount, &req, sm, buf_size, 0);

	// If buffer too small, reallocate in 4KB increments
	while (ret < 0 && errno == EOVERFLOW) {
		buf_size += 4096;
		sm = PyMem_RawRealloc(sm, buf_size);
		if (sm == NULL) {
			errno = ENOMEM;
			return NULL;
		}

		ret = syscall(__NR_statmount, &req, sm, buf_size, 0);
	}

	if (ret < 0) {
		PyMem_RawFree(sm);
		return NULL;
	}

	return sm;
}

int init_mount_types(PyObject *module)
{
	truenas_os_state_t *state = get_truenas_os_state(module);
	if (state == NULL) {
		return -1;
	}

	/* Create type dynamically and store in module state */
	state->StatmountResultType = (PyObject *)PyStructSequence_NewType(&statmount_result_desc);
	if (state->StatmountResultType == NULL) {
		return -1;
	}

	if (PyModule_AddObjectRef(module, "StatmountResult", state->StatmountResultType) < 0) {
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
#ifdef STATMOUNT_FS_SUBTYPE
	PyModule_AddIntConstant(module, "STATMOUNT_FS_SUBTYPE", STATMOUNT_FS_SUBTYPE);
#endif
#ifdef STATMOUNT_SB_SOURCE
	PyModule_AddIntConstant(module, "STATMOUNT_SB_SOURCE", STATMOUNT_SB_SOURCE);
#endif
#ifdef STATMOUNT_OPT_ARRAY
	PyModule_AddIntConstant(module, "STATMOUNT_OPT_ARRAY", STATMOUNT_OPT_ARRAY);
#endif
#ifdef STATMOUNT_OPT_SEC_ARRAY
	PyModule_AddIntConstant(module, "STATMOUNT_OPT_SEC_ARRAY", STATMOUNT_OPT_SEC_ARRAY);
#endif
#ifdef STATMOUNT_SUPPORTED_MASK
	PyModule_AddIntConstant(module, "STATMOUNT_SUPPORTED_MASK", STATMOUNT_SUPPORTED_MASK);
#endif
#ifdef STATMOUNT_MNT_UIDMAP
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_UIDMAP", STATMOUNT_MNT_UIDMAP);
#endif
#ifdef STATMOUNT_MNT_GIDMAP
	PyModule_AddIntConstant(module, "STATMOUNT_MNT_GIDMAP", STATMOUNT_MNT_GIDMAP);
#endif

	// Add STATMOUNT_ALL convenience constant (includes all available flags except UIDMAP/GIDMAP)
	PyModule_AddIntConstant(module, "STATMOUNT_ALL",
		STATMOUNT_SB_BASIC | STATMOUNT_MNT_BASIC | STATMOUNT_PROPAGATE_FROM |
		STATMOUNT_MNT_ROOT | STATMOUNT_MNT_POINT | STATMOUNT_FS_TYPE |
		STATMOUNT_MNT_NS_ID | STATMOUNT_MNT_OPTS
#ifdef STATMOUNT_FS_SUBTYPE
		| STATMOUNT_FS_SUBTYPE
#endif
#ifdef STATMOUNT_SB_SOURCE
		| STATMOUNT_SB_SOURCE
#endif
#ifdef STATMOUNT_OPT_ARRAY
		| STATMOUNT_OPT_ARRAY
#endif
#ifdef STATMOUNT_OPT_SEC_ARRAY
		| STATMOUNT_OPT_SEC_ARRAY
#endif
#ifdef STATMOUNT_SUPPORTED_MASK
		| STATMOUNT_SUPPORTED_MASK
#endif
	);

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

	// Add MS_* mount flags (user-facing flags only)
	PyModule_AddIntConstant(module, "MS_RDONLY", MS_RDONLY);
	PyModule_AddIntConstant(module, "MS_NOSUID", MS_NOSUID);
	PyModule_AddIntConstant(module, "MS_NODEV", MS_NODEV);
	PyModule_AddIntConstant(module, "MS_NOEXEC", MS_NOEXEC);
	PyModule_AddIntConstant(module, "MS_SYNCHRONOUS", MS_SYNCHRONOUS);
	PyModule_AddIntConstant(module, "MS_REMOUNT", MS_REMOUNT);
	PyModule_AddIntConstant(module, "MS_DIRSYNC", MS_DIRSYNC);
	PyModule_AddIntConstant(module, "MS_NOSYMFOLLOW", MS_NOSYMFOLLOW);
	PyModule_AddIntConstant(module, "MS_NOATIME", MS_NOATIME);
	PyModule_AddIntConstant(module, "MS_NODIRATIME", MS_NODIRATIME);
	PyModule_AddIntConstant(module, "MS_BIND", MS_BIND);
	PyModule_AddIntConstant(module, "MS_MOVE", MS_MOVE);
	PyModule_AddIntConstant(module, "MS_REC", MS_REC);
	PyModule_AddIntConstant(module, "MS_UNBINDABLE", MS_UNBINDABLE);
	PyModule_AddIntConstant(module, "MS_PRIVATE", MS_PRIVATE);
	PyModule_AddIntConstant(module, "MS_SLAVE", MS_SLAVE);
	PyModule_AddIntConstant(module, "MS_SHARED", MS_SHARED);
	PyModule_AddIntConstant(module, "MS_RELATIME", MS_RELATIME);
	PyModule_AddIntConstant(module, "MS_STRICTATIME", MS_STRICTATIME);
	PyModule_AddIntConstant(module, "MS_LAZYTIME", MS_LAZYTIME);

	return 0;
}
