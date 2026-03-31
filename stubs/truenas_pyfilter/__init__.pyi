"""Type stubs for truenas_pyfilter module."""

from typing import Any, Iterable


class CompiledFilters:
    """Pre-compiled filter tree produced by compile_filters()."""
    def __repr__(self) -> str: ...


class CompiledOptions:
    """Pre-compiled options produced by compile_options()."""
    def __repr__(self) -> str: ...


def match(
    item: Any,
    *,
    filters: CompiledFilters,
) -> bool:
    """Test whether a single item matches all compiled filters."""
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


def compile_filters(filters: list[Any]) -> CompiledFilters:
    """Pre-compile a query-filters list into a CompiledFilters object."""
    ...


def compile_options(
    *,
    get: bool = False,
    count: bool = False,
    select: list[str | list[Any]] | None = None,
    order_by: list[str] | None = None,
    offset: int = 0,
    limit: int = 0,
) -> CompiledOptions:
    """Pre-parse query-options into a CompiledOptions object."""
    ...
