# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Pydantic-model tests for the truenas_pyfilter filter_list implementation.

Split out of test_filter_list.py so pydantic is a hard import: a missing or
broken pydantic surfaces as a collection error that CI flags, rather than being
silently skipped via pytest.importorskip().

Covers:
  - eval_simple_from() pydantic fast path (stored fields read from __dict__,
    fallback to getattr for computed/extra/private attrs)
  - compile_filters(model=...) alias resolution
  - compile_options(model=...) alias resolution
"""
from __future__ import annotations

import dataclasses

import pydantic
import pytest

from truenas_pyfilter import (
    compile_filters,
    compile_options,
    tnfilter,
    match,
)


# ── Convenience wrapper ───────────────────────────────────────────────────────


def fl(data, filters, *, model=None, **co_kwargs):
    """Compile filters + options and run tnfilter. Returns the result list."""
    cf = compile_filters(filters or [], model=model)
    co = compile_options(**co_kwargs)
    return tnfilter(data, filters=cf, options=co)


BASIC = [
    {"id": 1, "name": "alice", "score": 100, "active": True,  "tags": ["a", "b"]},
    {"id": 2, "name": "bob",   "score": 85,  "active": True,  "tags": ["b", "c"]},
    {"id": 3, "name": "carol", "score": 90,  "active": False, "tags": ["a", "c"]},
    {"id": 4, "name": "dave",  "score": 70,  "active": False, "tags": ["d"]},
    {"id": 5, "name": "eve",   "score": 95,  "active": True,  "tags": ["a", "b", "c"]},
]


# ── pydantic-model fast path ──────────────────────────────────────────────────
#
# eval_simple_from() has a fast path that reads stored fields straight from a
# pydantic model's instance __dict__ (skipping the __getattr__-hook wrapper that
# pydantic installs as tp_getattro), falling back to getattr for everything not
# stored there. These tests pin the fast path's correctness end to end.


def _models():
    Field = pydantic.Field

    class Inner(pydantic.BaseModel):
        val: int

    class M(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(extra="allow")
        name: str
        age: int
        inner: Inner
        _secret: int = pydantic.PrivateAttr(default=0)

        @pydantic.computed_field
        @property
        def label(self) -> str:
            return f"{self.name}-{self.age}"

    a = M(name="alice", age=30, inner={"val": 1}, bonus=100)
    b = M(name="bob", age=20, inner={"val": 2}, bonus=200)
    a._secret = 7
    b._secret = 8
    return [a, b]


def test_pydantic_stored_field_fast_path():
    data = _models()
    out = fl(data, [["age", ">", 25]], model=type(data[0]))
    assert [o.name for o in out] == ["alice"]
    # original instances are returned, not copies/dicts
    assert all(type(o).__name__ == "M" for o in out)


def test_pydantic_nested_model_path():
    data = _models()
    out = fl(data, [["inner.val", "=", 2]], model=type(data[0]))
    assert [o.name for o in out] == ["bob"]


def test_pydantic_computed_field_falls_back_to_getattr():
    # computed_field is a property -> not in __dict__ -> getattr fallback
    data = _models()
    out = fl(data, [["label", "=", "alice-30"]], model=type(data[0]))
    assert [o.name for o in out] == ["alice"]


def test_pydantic_extra_field_falls_back_to_getattr():
    # extra='allow' fields live in __pydantic_extra__, not __dict__
    data = _models()
    out = fl(data, [["bonus", "=", 200]], model=type(data[0]))
    assert [o.name for o in out] == ["bob"]


def test_pydantic_private_attr_falls_back_to_getattr():
    # private attrs live in __pydantic_private__, not __dict__
    data = _models()
    out = fl(data, [["_secret", "=", 7]], model=type(data[0]))
    assert [o.name for o in out] == ["alice"]


def test_pydantic_missing_field_no_match():
    data = _models()
    assert fl(data, [["nonexistent", "=", 1]], model=type(data[0])) == []


def test_pydantic_match_single_item():
    m = _models()[0]
    cf = compile_filters([["age", "=", 30]], model=type(m))
    assert match(m, filters=cf) is m
    assert match(m, filters=compile_filters([["age", "=", 99]], model=type(m))) is None


def test_pydantic_filtering_without_model_is_rejected():
    # A filter compiled without model= must refuse pydantic model instances
    # rather than silently matching against unresolved attribute names.
    data = _models()
    cf = compile_filters([["age", ">", 25]])
    co = compile_options()
    with pytest.raises(TypeError, match="model="):
        tnfilter(data, filters=cf, options=co)
    with pytest.raises(TypeError, match="model="):
        match(data[0], filters=cf)


def test_pydantic_empty_filter_list_bypasses_model_guard():
    data = _models()
    cf = compile_filters([])
    co = compile_options(model=type(data[0]))
    assert tnfilter(data, filters=cf, options=co) == data
    assert match(data[0], filters=cf) is data[0]


def test_pydantic_wrong_model_instance_is_rejected():
    # A filter compiled for model A, run over instances of a *different* model
    # B, must refuse rather than silently misresolving A's alias paths against
    # B. Both models share field names so the failure would otherwise be quiet.
    class A(pydantic.BaseModel):
        name: str = pydantic.Field(alias="nm")

    class B(pydantic.BaseModel):
        name: str = pydantic.Field(alias="nm")

    cf = compile_filters([["nm", "=", "x"]], model=A)
    co = compile_options()
    with pytest.raises(TypeError, match="not the compiled model A"):
        tnfilter([B(nm="x")], filters=cf, options=co)
    with pytest.raises(TypeError, match="not the compiled model A"):
        match(B(nm="x"), filters=cf)


def test_pydantic_subclass_instance_is_rejected():
    # Exact class only: a subclass may redefine aliases, so even an instance of
    # a subclass of the compiled model is refused.
    class A(pydantic.BaseModel):
        name: str = pydantic.Field(alias="nm")

    class ASub(A):
        pass

    cf = compile_filters([["nm", "=", "x"]], model=A)
    co = compile_options()
    with pytest.raises(TypeError, match="not the compiled model A"):
        tnfilter([ASub(nm="x")], filters=cf, options=co)
    with pytest.raises(TypeError, match="not the compiled model A"):
        match(ASub(nm="x"), filters=cf)


def test_pydantic_correct_model_instance_is_accepted():
    # The exact compiled model (and a matching item) passes through unchanged.
    class A(pydantic.BaseModel):
        name: str = pydantic.Field(alias="nm")

    cf = compile_filters([["nm", "=", "x"]], model=A)
    co = compile_options()
    item = A(nm="x")
    assert tnfilter([item], filters=cf, options=co) == [item]
    assert match(item, filters=cf) is item


def test_pydantic_wrong_model_in_mixed_list_is_rejected():
    # A correct model instance followed by a wrong one: the wrong instance is
    # still caught mid-iteration.
    class A(pydantic.BaseModel):
        name: str = pydantic.Field(alias="nm")

    class B(pydantic.BaseModel):
        name: str = pydantic.Field(alias="nm")

    cf = compile_filters([["nm", "=", "x"]], model=A)
    co = compile_options()
    with pytest.raises(TypeError, match="not the compiled model A"):
        tnfilter([A(nm="x"), B(nm="x")], filters=cf, options=co)


def test_pydantic_heterogeneous_list_cache_does_not_misfire():
    # dict, pydantic model and dataclass interleaved: the per-type inline
    # cache must recompute its verdict whenever the item type changes.
    @dataclasses.dataclass
    class D:
        age: int

    data = _models()
    mixed = [{"age": 50}, data[0], D(age=5), {"age": 1}, data[1]]
    out = fl(mixed, [["age", ">", 10]], model=type(data[0]))
    ages = [o["age"] if isinstance(o, dict) else o.age for o in out]
    assert ages == [50, 30, 20]


# ── compile_filters(model=...) alias resolution ───────────────────────────────
#
# When a pydantic model class is passed, compile_filters resolves each filter
# path from field alias -> attribute name at compile time (nested models
# followed), so the compiled filter applies directly to model instances.

def _aliased_models():
    Field = pydantic.Field

    class Inner(pydantic.BaseModel):
        val: int = Field(alias="theVal")

    class Item(pydantic.BaseModel):
        name: str = Field(alias="userName")
        age: int = Field(alias="years")
        inner: Inner = Field(alias="nested")
        kids: list[Inner] = Field(default_factory=list, alias="children")

    data = [
        Item(userName="alice", years=30, nested={"theVal": 1}, children=[{"theVal": 7}]),
        Item(userName="bob", years=20, nested={"theVal": 2}, children=[{"theVal": 9}]),
    ]
    return Item, data


def _run_model(filters, model, data):
    cf = compile_filters(filters, model=model)
    return [o.name for o in tnfilter(data, filters=cf, options=compile_options())]


def test_compile_model_flat_alias():
    Item, data = _aliased_models()
    assert _run_model([["years", ">", 25]], Item, data) == ["alice"]


def test_compile_model_nested_alias():
    Item, data = _aliased_models()
    assert _run_model([["nested.theVal", "=", 2]], Item, data) == ["bob"]


def test_compile_model_list_index_alias():
    Item, data = _aliased_models()
    assert _run_model([["children.0.theVal", "=", 7]], Item, data) == ["alice"]


def test_compile_model_wildcard_alias():
    Item, data = _aliased_models()
    assert _run_model([["children.*.theVal", "=", 9]], Item, data) == ["bob"]


def test_compile_model_or_alias():
    Item, data = _aliased_models()
    out = _run_model(
        [["OR", [[["years", "=", 20]], [["userName", "=", "alice"]]]]], Item, data
    )
    assert sorted(out) == ["alice", "bob"]


def test_compile_model_attribute_name_passthrough():
    # the real attribute name still resolves (maps to itself)
    Item, data = _aliased_models()
    assert _run_model([["age", ">", 25]], Item, data) == ["alice"]


def test_compile_model_unknown_key_raises():
    # an unknown alias against a strict model is a bug -> ValueError
    Item, _ = _aliased_models()
    with pytest.raises(ValueError, match="not a field or alias"):
        compile_filters([["nonexistentAlias", "=", 1]], model=Item)


def test_compile_model_unknown_nested_key_raises():
    Item, _ = _aliased_models()
    with pytest.raises(ValueError, match="not a field or alias"):
        compile_filters([["nested.bogus", "=", 1]], model=Item)


def test_compile_model_extra_allow_passthrough():
    # extra='allow' models may carry fields outside model_fields; an unknown
    # component is left as-is (not an error) and resolves at runtime via getattr
    class Loose(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(extra="allow")
        name: str = pydantic.Field(alias="userName")

    data = [Loose(userName="alice", bonus=100), Loose(userName="bob", bonus=200)]
    cf = compile_filters([["bonus", "=", 200]], model=Loose)
    out = tnfilter(data, filters=cf, options=compile_options())
    assert [o.name for o in out] == ["bob"]


def test_compile_model_repr_keeps_original_filters():
    Item, _ = _aliased_models()
    cf = compile_filters([["years", ">", 25]], model=Item)
    assert repr(cf) == "CompiledFilters([['years', '>', 25]])"


def test_compile_model_rejects_non_model():
    with pytest.raises(TypeError, match="pydantic model"):
        compile_filters([["x", "=", 1]], model=int)


def test_compile_model_none_is_noop():
    # model=None behaves exactly like omitting it (no resolution)
    cf = compile_filters([["id", "=", 1]], model=None)
    assert tnfilter(BASIC, filters=cf, options=compile_options())[0]["id"] == 1


# ── compile_options(model=...) alias resolution ───────────────────────────────
#
# Mirrors compile_filters(model=...): order_by/select field paths are resolved
# from field alias -> attribute name at compile time, so they apply directly to
# model instances. compile_filters([], model=...) is needed alongside so that
# tnfilter accepts the model instances.

def _opts_run(model, data, **opts_kwargs):
    cf = compile_filters([], model=model)
    co = compile_options(model=model, **opts_kwargs)
    return tnfilter(data, filters=cf, options=co)


def test_options_model_order_by_alias():
    Item, data = _aliased_models()
    out = _opts_run(Item, data, order_by=["userName"])
    assert [o.name for o in out] == ["alice", "bob"]


def test_options_model_order_by_reverse_nested_alias():
    Item, data = _aliased_models()
    out = _opts_run(Item, data, order_by=["-nested.theVal"])
    assert [o.name for o in out] == ["bob", "alice"]


def test_options_model_select_alias_projects_attribute_names():
    Item, data = _aliased_models()
    out = _opts_run(Item, data, select=["userName", "years"])
    assert out == [
        {"name": "alice", "age": 30},
        {"name": "bob", "age": 20},
    ]


def test_options_model_select_nested_alias():
    Item, data = _aliased_models()
    out = _opts_run(Item, data, select=["nested.theVal"])
    assert out == [{"inner": {"val": 1}}, {"inner": {"val": 2}}]


def test_options_model_unknown_order_by_alias_raises():
    Item, _ = _aliased_models()
    with pytest.raises(ValueError, match="not a field or alias"):
        compile_options(order_by=["bogus"], model=Item)


def test_options_model_unknown_select_alias_raises():
    Item, _ = _aliased_models()
    with pytest.raises(ValueError, match="not a field or alias"):
        compile_options(select=["bogus"], model=Item)


def test_options_model_rejects_non_model():
    with pytest.raises(TypeError, match="pydantic model"):
        compile_options(order_by=["x"], model=int)


def test_options_model_none_is_noop():
    # model=None behaves exactly like omitting it (no resolution); dict items
    # sort by their literal key.
    co = compile_options(order_by=["id"], model=None)
    out = tnfilter(BASIC, filters=compile_filters([]), options=co)
    assert [o["id"] for o in out] == sorted(o["id"] for o in BASIC)
