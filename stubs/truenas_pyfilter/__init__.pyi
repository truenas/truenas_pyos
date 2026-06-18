"""Type stubs for truenas_pyfilter module."""

from typing import Any, Iterable, final

# order_by prefix constants
FILTER_ORDER_NULLS_FIRST_PREFIX: str
FILTER_ORDER_NULLS_LAST_PREFIX: str
FILTER_ORDER_REVERSE_PREFIX: str

# filter operator constants
FILTER_OP_EQ: str
FILTER_OP_NE: str
FILTER_OP_GT: str
FILTER_OP_GE: str
FILTER_OP_LT: str
FILTER_OP_LE: str
FILTER_OP_REGEX: str
FILTER_OP_IN: str
FILTER_OP_NOT_IN: str
FILTER_OP_REGEX_IN: str
FILTER_OP_REGEX_NOT_IN: str
FILTER_OP_STARTSWITH: str
FILTER_OP_NOT_STARTSWITH: str
FILTER_OP_ENDSWITH: str
FILTER_OP_NOT_ENDSWITH: str
FILTER_OP_CI_PREFIX: str


@final
class CompiledFilters:
    """Pre-compiled filter tree produced by compile_filters()."""
    def __repr__(self) -> str: ...


@final
class CompiledOptions:
    """Pre-compiled options produced by compile_options()."""
    get: bool
    count: bool
    select: list[str | list[Any]]
    order_by: list[str]
    offset: int
    limit: int
    model: type[Any] | None
    def __repr__(self) -> str: ...


def match(
    item: Any,
    *,
    filters: CompiledFilters,
    options: CompiledOptions | None = None,
) -> Any | None:
    """Test whether a single item matches all compiled filters.

    Returns None if the item does not match. If it matches and options
    contains a select spec, returns a new projected dict. Otherwise
    returns the original item unchanged.
    """
    ...


def tnfilter(
    data: Iterable[Any],
    *,
    filters: CompiledFilters,
    options: CompiledOptions,
) -> list[Any]:
    """Filter an iterable using pre-compiled C-level filters.

    Both arguments must be pre-compiled objects from compile_filters() and
    compile_options() respectively.
    """
    ...


def compile_filters(
    filters: list[Any],
    *,
    model: type[Any] | None = None,
) -> CompiledFilters:
    """Pre-compile a query-filters list into a CompiledFilters object.

    Pass ``model`` (the pydantic model class) when the compiled filter will be
    run against instances of that model. It is required to filter pydantic
    models: a filter compiled without ``model`` raises ``TypeError`` if handed
    a model instance. Field aliases are resolved to attribute names at compile
    time.
    """
    ...


def compile_options(
    *,
    get: bool = False,
    count: bool = False,
    select: list[str | list[Any]] | None = None,
    order_by: list[str] | None = None,
    offset: int = 0,
    limit: int = 0,
    model: type[Any] | None = None,
) -> CompiledOptions:
    """Pre-parse query-options into a CompiledOptions object.

    Pass ``model`` (the pydantic model class) when the options will be applied
    to instances of that model: ``select``/``order_by`` field aliases are
    resolved to attribute names at compile time, the same way
    ``compile_filters`` resolves filter paths.
    """
    ...
