// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _USERNS_H_
#define _USERNS_H_

extern int init_userns_type(PyObject *module);
extern PyObject *do_create_idmap_mapping(unsigned long inside,
                                          unsigned long outside,
                                          unsigned long length);
extern PyObject *do_create_idmap_userns(PyObject *uid_seq, PyObject *gid_seq);

#endif /* _USERNS_H_ */
