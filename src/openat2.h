// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _OPENAT2_H_
#define _OPENAT2_H_

#include <linux/openat2.h>

extern PyObject *do_openat2(int dirfd, const char *pathname,
                             uint64_t flags, uint64_t mode, uint64_t resolve);
extern int init_openat2_constants(PyObject *module);

/*
 * C wrapper for openat2() syscall (non-Python)
 * Returns file descriptor on success, -1 on error (errno set)
 */
int openat2_impl(int dirfd, const char *pathname, int flags, uint64_t resolve_flags);

#endif /* _OPENAT2_H_ */
