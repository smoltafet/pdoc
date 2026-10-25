import builtins
import dataclasses
from pathlib import Path
import types
from unittest.mock import patch

import pytest

from pdoc import extract
from pdoc.doc import Class
from pdoc.doc import Module
from pdoc.doc import Variable
from pdoc.doc import _environ_lookup
from pdoc.doc_types import empty

here = Path(__file__).parent


def test_repr_tb(monkeypatch):
    m = Module(dataclasses)
    with patch("pdoc.doc._docstr", side_effect=ValueError):
        with pytest.raises(RuntimeError, match="Error in dataclasses's repr!"):
            repr(m)


def test_order():
    m = Module(dataclasses)
    m2 = Module(pytest)
    assert m < m2


def test_attrs():
    mod = extract.load_module(extract.parse_spec(here / "testdata" / "demo_long.py"))

    m = Module(mod)
    assert m.variables
    assert m.classes
    assert m.functions
    assert m.source_lines

    c = m.members["Foo"]
    assert isinstance(c, Class)
    assert c.class_variables
    assert c.instance_variables
    assert c.classmethods
    assert c.staticmethods
    assert c.methods

    e = m.members["EnumDemo"]
    assert isinstance(e, Class)
    v = e.members["RED"]
    assert isinstance(v, Variable)
    assert v.is_enum_member

    c = m.members["FOO_CONSTANT"]
    assert isinstance(c, Variable)
    assert not c.is_enum_member


def test_all_with_import_err():
    mod = extract.load_module(extract.parse_spec(here / "import_err"))
    m = Module(mod)
    with pytest.warns(
        UserWarning,
        match="Found 'err' in test.import_err.__all__, but it does not resolve: Error importing test.import_err",
    ):
        assert m.members


def test_all_with_objects_instead_of_strings():
    class Foo:
        pass

    mod = types.ModuleType("mod")
    mod.__dict__["Foo"] = Foo
    mod.__dict__["__all__"] = [Foo]  # should be ["Foo"]

    m = Module(mod)
    assert m.members


def test_var_with_raising_repr():
    class Raising:
        def __repr__(self):
            raise RuntimeError

    v = Variable(
        "module",
        "var",
        taken_from=("module", "var"),
        docstring="",
        annotation=empty,
        default_value=Raising(),
    )
    with pytest.warns(
        match=r"repr\(module\.var\) raised an exception \(RuntimeError\(\)\)"
    ):
        assert not v.default_value_str


def test_class_with_raising_getattr():
    class _Raise(type):
        def __getattribute__(cls, key):
            if key == "__annotations__":
                raise RuntimeError
            return super().__getattribute__(key)

    class RaisingGetAttr(metaclass=_Raise):
        pass

    c = Class("test", "Raising", RaisingGetAttr, ("test", "Raising"))
    with pytest.warns(UserWarning, match="getattr.+raised an exception"):
        assert c.members


def test_builtin_source():
    m = Module(builtins)
    assert m.source_file is None
    assert m.source_lines is None


def test_raising_getdoc():
    class FooMeta(type):
        def __getattribute__(cls, name):
            if name == "__doc__":
                raise RuntimeError
            return super().__getattribute__(name)

    class Foo(metaclass=FooMeta):
        pass

    x = Class(Foo.__module__, Foo.__qualname__, Foo, (Foo.__module__, Foo.__qualname__))
    with pytest.warns(UserWarning, match="inspect.getdoc(.+) raised an exception"):
        assert x.docstring == ""


def test_raising_submodules():
    f = here / "syntax_err" / "syntax_err.py"
    f.write_bytes(b"class")

    try:
        extract.parse_spec(f.parent)
        m = Module.from_name("test.syntax_err")
        with pytest.warns(UserWarning, match="Error importing"):
            assert m.submodules
    finally:
        f.write_bytes(b"# syntax error will be inserted by test here\n")


def test_default_value_masks_env_vars(monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "correct horse battery staple")
    monkeypatch.setenv("VERSION_NUMBER", "42.0.1")
    _environ_lookup.cache_clear()
    try:
        v1 = Variable(
            "module",
            "var",
            taken_from=("module", "var"),
            docstring="",
            annotation=empty,
            default_value="correct horse battery staple",
        )
        with pytest.warns(
            match=r"The default value of module.var matches the \$SUPER_SECRET_TOKEN environment variable."
        ):
            assert v1.default_value_str == "$SUPER_SECRET_TOKEN"

        v2 = Variable(
            "module",
            "version",
            taken_from=("module", "version"),
            docstring="",
            annotation=empty,
            default_value="42.0.1",
        )
        assert v2.default_value_str == "'42.0.1'"
    finally:
        _environ_lookup.cache_clear()


def test_source_file_method():
    mod = extract.load_module(extract.parse_spec(here / "testdata" / "demo_long.py"))

    m = Module(mod)

    assert m.members["Foo"].members["a_cached_function"].source_file == (
        here / "testdata" / "demo_long.py"
    )


def test_namedtuple_forward_ref_init_signature():
    """Forward references in a NamedTuple's fields must resolve in the
    synthesized __init__/__new__ signature as well (issue #765).

    The __new__ that typing.NamedTuple generates lives in a throwaway
    "namedtuple_<Name>" module, so inspect.getmodule() returns None for
    it and annotations used to be evaluated against an empty namespace,
    producing an "Import of <Name> failed" warning for every local type.
    """
    import sys
    import types
    import warnings

    mod = types.ModuleType("namedtuple_fwd_ref_test")
    code = """
from __future__ import annotations
from typing import NamedTuple


class Foo(NamedTuple):
    b: Bar


class Bar:
    pass
"""
    exec(compile(code, "namedtuple_fwd_ref_test.py", "exec"), mod.__dict__)
    sys.modules[mod.__name__] = mod
    try:
        m = Module(mod)
        foo = m.members["Foo"]
        assert isinstance(foo, Class)
        init = foo.members["__init__"]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sig = init.signature
        assert caught == []
        assert sig.parameters["b"].annotation is mod.Bar
        # The field variable itself resolves, too.
        assert foo.members["b"].annotation is mod.Bar
    finally:
        del sys.modules[mod.__name__]
