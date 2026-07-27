# SPDX-License-Identifier: LGPL-3.0-or-later
"""Kernel capability discovery: query() and supported_ops()."""

import pytest

from truenas_os import uring

from .conftest import require_query, requires_io_uring


# Opcodes the reactor actually issues. If any of these is missing the module
# cannot work at all, so assert them rather than skipping.
REQUIRED_OPS = (
    'IORING_OP_OPENAT2',
    'IORING_OP_READ',
    'IORING_OP_WRITE',
    'IORING_OP_FSYNC',
    'IORING_OP_STATX',
    'IORING_OP_CLOSE',
    'IORING_OP_ASYNC_CANCEL',
)

# Opcodes the M2 metadata/xattr sweep will need. Not required yet.
FUTURE_OPS = (
    'IORING_OP_RENAMEAT',
    'IORING_OP_UNLINKAT',
    'IORING_OP_MKDIRAT',
    'IORING_OP_SYMLINKAT',
    'IORING_OP_LINKAT',
    'IORING_OP_FGETXATTR',
    'IORING_OP_FSETXATTR',
    'IORING_OP_FALLOCATE',
)


@requires_io_uring
def test_supported_ops_returns_frozenset():
    ops = uring.supported_ops()
    assert isinstance(ops, frozenset)
    assert ops, 'probe reported no supported opcodes'
    assert all(isinstance(op, int) for op in ops)


@requires_io_uring
@pytest.mark.parametrize('name', REQUIRED_OPS)
def test_required_opcode_supported(name):
    """Every opcode the reactor submits must exist on this kernel."""
    ops = uring.supported_ops()
    assert getattr(uring, name) in ops, '%s is not supported' % name


@requires_io_uring
def test_supported_ops_bounded_by_op_last():
    ops = uring.supported_ops()
    assert max(ops) < uring.IORING_OP_LAST


def test_query_returns_none_or_queryresult():
    """query() is a capability probe, so absence is a result, not an error.

    IORING_REGISTER_QUERY landed in 6.18. On anything older the register call
    returns EINVAL and query() reports None; supported_ops() is the fallback.
    """
    q = require_query()
    assert isinstance(q, uring.QueryResult)


def test_query_fields_are_self_consistent():
    q = require_query()

    assert q.nr_request_opcodes > 0
    assert q.nr_register_opcodes > 0
    assert q.nr_query_opcodes > 0

    # The reported opcode count is IORING_OP_LAST as the *kernel* sees it.
    # liburing's header may be newer or older, so only require that every
    # opcode we submit falls inside the kernel's range.
    for name in REQUIRED_OPS:
        assert getattr(uring, name) < q.nr_request_opcodes

    # Personalities are the foundation of the credential model.
    assert q.feature_flags & uring.IORING_FEAT_CUR_PERSONALITY

    # The SQE flags we set must be accepted.
    assert q.sqe_flags & uring.IOSQE_FIXED_FILE


@requires_io_uring
def test_query_agrees_with_probe():
    """The two discovery mechanisms must not contradict each other."""
    q = require_query()

    ops = uring.supported_ops()
    # Everything the probe reports must be within the queried opcode count.
    assert max(ops) < q.nr_request_opcodes


def test_queryresult_is_a_structseq():
    assert uring.QueryResult.n_fields == 7
    assert 'feature_flags' in uring.QueryResult.__match_args__


def test_personality_id_space_constant():
    """0 is never allocated, so it is unambiguously 'ambient credentials'."""
    assert uring.URING_PERSONALITY_MAX == 65535
