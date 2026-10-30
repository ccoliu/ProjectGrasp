import json
import textwrap

import pytest

from grasp.cli import main
from grasp.model import MODULE_SCOPE
from grasp.parsers import PythonParser, parser_for


def parse(code: str):
    return PythonParser().parse(textwrap.dedent(code).encode(), "t.py")


def defs_by_qualname(result):
    return {d.qualname: d for d in result.definitions}


def calls_of(result, caller):
    return [c.text for c in result.calls if c.caller == caller]


def test_definitions_and_qualnames():
    result = parse(
        """
        def top():
            def inner():
                pass

        class Service:
            def run(self):
                pass

            class Config:
                def load(self):
                    pass

        async def fetch():
            pass
        """
    )
    defs = defs_by_qualname(result)
    assert set(defs) == {
        "top",
        "top.<locals>.inner",
        "Service",
        "Service.run",
        "Service.Config",
        "Service.Config.load",
        "fetch",
    }
    assert defs["top"].kind == "function"
    assert defs["top.<locals>.inner"].kind == "function"
    assert defs["top.<locals>.inner"].parent == "top"
    assert defs["Service"].kind == "class"
    assert defs["Service"].parent is None
    assert defs["Service.run"].kind == "method"
    assert defs["Service.Config.load"].kind == "method"
    assert defs["fetch"].is_async
    assert not defs["top"].is_async
    assert (defs["Service.run"].line, defs["Service.run"].column) == (7, 5)
    assert defs["Service"].end_line == 12


def test_call_kinds():
    result = parse(
        """
        def f(self):
            helper()
            self.save()
            os.path.join(a, b)
            handlers[0]()
            make()()
        """
    )
    calls = {c.text: c for c in result.calls}
    assert calls["helper"].kind == "name"
    assert calls["helper"].callee == "helper"
    assert calls["helper"].receiver is None
    assert (calls["self.save"].callee, calls["self.save"].receiver) == ("save", "self")
    assert (calls["os.path.join"].callee, calls["os.path.join"].receiver) == ("join", "os.path")
    assert calls["handlers[0]"].kind == "other"
    assert calls["handlers[0]"].callee is None
    assert calls["make()"].kind == "other"
    assert calls["make"].kind == "name"


def test_calls_are_attributed_to_evaluating_scope():
    result = parse(
        """
        setup()

        @app.route(path())
        def view(x=default()) -> annotate():
            body()
            return [item() for item in items()]

        class Model(base()):
            field = column()

            def save(self):
                super().save()
        """
    )
    assert calls_of(result, MODULE_SCOPE) == [
        "setup",
        "app.route",
        "path",
        "default",
        "annotate",
        "base",
    ]
    assert calls_of(result, "view") == ["body", "item", "items"]
    assert calls_of(result, "Model") == ["column"]
    assert calls_of(result, "Model.save") == ["super().save", "super"]


def test_nested_calls_in_arguments():
    result = parse("print(len(str(x)))\n")
    assert [c.text for c in result.calls] == ["print", "len", "str"]


def test_decorators_recorded():
    result = parse(
        """
        class A:
            @staticmethod
            @functools.lru_cache(maxsize=None)
            def cached():
                pass

            @property
            def value(self):
                pass
        """
    )
    defs = defs_by_qualname(result)
    assert defs["A.cached"].decorators == ("staticmethod", "functools.lru_cache(maxsize=None)")
    assert defs["A.value"].decorators == ("property",)
    # The definition line is the `def`, not the first decorator.
    assert defs["A.cached"].line == 5


def test_tolerates_syntax_errors():
    result = parse(
        """
        def ok():
            first()

        def broken(:
            pass

        def after():
            second()
        """
    )
    assert result.has_errors
    assert "ok" in defs_by_qualname(result)
    assert "after" in defs_by_qualname(result)
    assert calls_of(result, "ok") == ["first"]
    assert calls_of(result, "after") == ["second"]


def test_columns_count_characters_not_bytes():
    result = parse('名稱 = "中文"; go()\n')
    (call,) = result.calls
    assert call.column == len('名稱 = "中文"; ') + 1


def test_deep_nesting_does_not_overflow():
    code = "x = " + "f(" * 2000 + ")" * 2000 + "\n"
    result = parse(code)
    assert len(result.calls) == 2000


def test_imports():
    result = parse(
        """
        import os
        import a.b as c, d.e
        from m import x as y, z
        from ..pkg.sub import q
        from . import r
        from .mod import *
        from __future__ import annotations

        def f():
            import json
        """
    )
    got = [(i.scope, i.module, i.name, i.alias, i.level, i.bound_name) for i in result.imports]
    assert got == [
        (MODULE_SCOPE, "os", None, None, 0, "os"),
        (MODULE_SCOPE, "a.b", None, "c", 0, "c"),
        (MODULE_SCOPE, "d.e", None, None, 0, "d"),
        (MODULE_SCOPE, "m", "x", "y", 0, "y"),
        (MODULE_SCOPE, "m", "z", None, 0, "z"),
        (MODULE_SCOPE, "pkg.sub", "q", None, 2, "q"),
        (MODULE_SCOPE, "", "r", None, 1, "r"),
        (MODULE_SCOPE, "mod", "*", None, 1, "*"),
        ("f", "json", None, None, 0, "json"),
    ]


def test_class_bases():
    result = parse("class K(Base, mod.Other, metaclass=Meta):\n    pass\n")
    (k,) = result.definitions
    assert k.bases == ("Base", "mod.Other")

def test_params_and_return_type():
    result = parse(
        """
        def f(self, a, b: int, c=1, d: str = "x", *args, e, **kw) -> Token:
            pass

        def g(x, /, *, y: "Q" = None, *rest: int, **opts: Any):
            pass
        """
    )
    f, g = result.definitions
    assert f.params == ("self", "a", "b: int", "c", "d: str", "*args", "e", "**kw")
    assert f.returns == "Token"
    assert g.params == ("x", 'y: "Q"', "*rest: int", "**opts: Any")
    assert g.returns is None


def test_main_guard():
    assert parse("if __name__ == '__main__':\n    main()\n").has_main_guard
    assert parse('if "__main__" == __name__:\n    main()\n').has_main_guard
    assert not parse("if DEBUG:\n    main()\n").has_main_guard
    assert not parse("def f():\n    if __name__ == '__main__':\n        pass\n").has_main_guard


def test_parser_registry():
    assert isinstance(parser_for("pkg/mod.py"), PythonParser)
    assert parser_for("main.cpp") is None


def test_cli_parse_outputs_json(tmp_path, capsys):
    src = tmp_path / "mod.py"
    src.write_text("def a():\n    b()\n", encoding="utf-8")
    assert main(["parse", str(src)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["language"] == "python"
    assert data["definitions"][0]["qualname"] == "a"
    assert data["calls"][0] == {
        "caller": "a",
        "callee": "b",
        "receiver": None,
        "kind": "name",
        "text": "b",
        "line": 2,
        "column": 5,
    }


@pytest.mark.parametrize("name", ["missing.py", "notes.txt"])
def test_cli_rejects_bad_input(tmp_path, name, capsys):
    path = tmp_path / name
    if name.endswith(".txt"):
        path.write_text("hi")
    assert main(["parse", str(path)]) == 2
    assert "grasp:" in capsys.readouterr().err
