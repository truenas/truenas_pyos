# Recursive file-tree copy and the file-level copy/clone primitives that
# back it.  Iteration is driven by truenas_os.iter_filesystem_contents
# (depth-first, GIL released, mountpoint-validated).
#
# - copy.py: file-level primitives (copy_permissions, copy_xattrs,
#   copyuserspace, copysendfile, clonefile, copyfile)
# - copytree.py: tree-level recursion (CopyFlags, CopyTreeOp, CopyJob,
#   CopyTreeConfig, CopyTreeStats, copytree)
from .copy import (
    MAX_RW_SZ,
    clonefile,
    copy_permissions,
    copy_xattrs,
    copyfile,
    copysendfile,
    copyuserspace,
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
    "clonefile",
    "copy_permissions",
    "copy_xattrs",
    "copyfile",
    "copysendfile",
    "copytree",
    "copyuserspace",
]
