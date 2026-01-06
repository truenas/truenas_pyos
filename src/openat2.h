// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _OPENAT2_H_
#define _OPENAT2_H_

#include <linux/openat2.h>

extern PyObject *do_openat2(int dirfd, const char *pathname,
                             uint64_t flags, uint64_t mode, uint64_t resolve);
extern int init_openat2_constants(PyObject *module);

#endif /* _OPENAT2_H_ */
