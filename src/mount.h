// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _MOUNT_H_
#define _MOUNT_H_

#include <Python.h>
#include <stdint.h>

#define LISTMOUNT_BATCH_SIZE 1024

// Core mount functions
PyObject *do_listmount(uint64_t mnt_id, uint64_t last_mnt_id, int reverse);
PyObject *do_statmount(uint64_t mnt_id, uint64_t mask);

// C wrapper for statmount() - returns pointer to statmount struct (caller must free)
// Returns NULL with errno set on error
struct statmount *statmount_impl(uint64_t mnt_id, uint64_t mask);

// Initialize mount types (StatmountResult) and constants
int init_mount_types(PyObject *module);

// Mount iterator functions
PyObject *create_mount_iterator(PyObject *args, PyObject *kwargs);
int init_mount_iter_type(PyObject *module);

#endif /* _MOUNT_H_ */
