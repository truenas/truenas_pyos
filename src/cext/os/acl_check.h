// SPDX-License-Identifier: LGPL-3.0-or-later

#ifndef _ACL_CHECK_H_
#define _ACL_CHECK_H_

extern int init_acl_check_types(PyObject *module);
extern PyObject *do_create_cred_entry(PyObject *id_name,
                                       unsigned long uid,
                                       unsigned long gid,
                                       PyObject *groups);
extern PyObject *do_check_path_access(PyObject *creds_seq,
                                       PyObject *components_seq,
                                       int path_must_exist);

#endif /* _ACL_CHECK_H_ */
