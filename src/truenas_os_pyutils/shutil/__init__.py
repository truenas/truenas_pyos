# Recursive file-tree copy and the file-level copy/clone primitives that
# back it.  Iteration is driven by truenas_os.iter_filesystem_contents
# (depth-first, GIL released, mountpoint-validated).
#
# - copy.py: file-level primitives (copy_permissions, copy_xattrs,
#   copy_file_userspace, copy_sendfile, clone_file, clone_or_copy_file)
# - copytree.py: tree-level recursion (CopyFlags, CopyTreeOp, CopyJob,
#   CopyTreeConfig, CopyTreeStats, copytree)
from .copy import (
    MAX_RW_SZ,
    clone_file,
    clone_or_copy_file,
    copy_file_userspace,
    copy_permissions,
    copy_sendfile,
    copy_xattrs,
)
from .copytree import (
    CLONETREE_ROOT_DEPTH,
    DEF_CP_FLAGS,
    CopyFlags,
    CopyTreeConfig,
    CopyTreeOp,
    CopyTreeStats,
    ReportingCallback,
    copytree,
)

__all__ = [
    "CLONETREE_ROOT_DEPTH",
    "DEF_CP_FLAGS",
    "MAX_RW_SZ",
    "CopyFlags",
    "CopyTreeConfig",
    "CopyTreeOp",
    "CopyTreeStats",
    "ReportingCallback",
    "clone_file",
    "clone_or_copy_file",
    "copy_file_userspace",
    "copy_permissions",
    "copy_sendfile",
    "copy_xattrs",
    "copytree",
]
