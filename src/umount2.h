// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _UMOUNT2_H_
#define _UMOUNT2_H_

extern PyObject *do_umount2(const char *target, int flags);
extern int init_umount2_constants(PyObject *module);

#endif /* _UMOUNT2_H_ */
