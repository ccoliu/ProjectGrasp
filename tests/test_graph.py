import json
import textwrap

import pytest

from grasp.cli import main
from grasp.graph import build_graph, module_name


def write_project(root, files: dict[str, str]):
    for rel, code in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(code), encoding="utf-8")
    return root


def edges(graph, confidence="certain"):
    return {(e.source, e.target) for e in graph.edges if e.confidence == confidence}


@pytest.fixture
def shop(tmp_path):
    return write_project(
        tmp_path,
        {
            "shop/__init__.py": """
                from .orders import place_order
            """,
            "shop/util.py": """
                def log(msg):
                    pass

                def slugify(text):
                    log(text)
            """,
            "shop/models.py": """
                from shop import util

                class Base:
                    def save(self):
                        util.log("save")

                class Order(Base):
                    def __init__(self, items):
                        self.items = items

                    def save(self):
                        self.validate()
                        super().save()

                    def validate(self):
                        pass

                    @classmethod
                    def empty(cls):
                        return cls.build([])

                    @staticmethod
                    def build(items):
                        return Order(items)
            """,
            "shop/orders.py": """
                import shop.util
                from .models import Order
                from . import models as m

                def place_order(items):
                    order = Order(items)
                    order.save()
                    shop.util.slugify("x")
                    m.Order.empty()
                    print(len(items))

                    def notify():
                        helper()

                    notify()

                def helper():
                    pass
            """,
            "main.py": """
                import json
                from shop import place_order

                def run():
                    place_order([])
                    json.dumps({})
                    unknown_function()

                if __name__ == "__main__":
                    run()
            """,
        },
    )


def test_module_names(tmp_path):
    write_project(tmp_path, {"src/pkg/__init__.py": "", "src/pkg/sub/__init__.py": "", "src/pkg/sub/mod.py": ""})
    assert module_name(tmp_path / "src/pkg/sub/mod.py") == ("pkg.sub.mod", False)
    assert module_name(tmp_path / "src/pkg/sub/__init__.py") == ("pkg.sub", True)
    assert module_name(tmp_path / "src/pkg/__init__.py") == ("pkg", True)


def test_nodes_include_modules_and_definitions(shop):
    graph = build_graph(shop)
    ids = {n.id for n in graph.nodes}
    assert {"main:<module>", "main:run", "shop.models:Order.save", "shop.orders:place_order.<locals>.notify"} <= ids
    node = next(n for n in graph.nodes if n.id == "shop.util:log")
    assert (node.kind, node.path, node.line) == ("function", "shop/util.py", 2)


def test_certain_edges(shop):
    found = edges(build_graph(shop))
    expected = {
        # same-file function
        ("shop.util:slugify", "shop.util:log"),
        # `import a.b` then a.b.f()
        ("shop.orders:place_order", "shop.util:slugify"),
        # constructor resolves to __init__ through `from .models import Order`
        ("shop.orders:place_order", "shop.models:Order.__init__"),
        # module alias plus class attribute: m.Order.empty()
        ("shop.orders:place_order", "shop.models:Order.empty"),
        # nested function, and a module-level function seen from inside it
        ("shop.orders:place_order", "shop.orders:place_order.<locals>.notify"),
        ("shop.orders:place_order.<locals>.notify", "shop.orders:helper"),
        # self / super() / cls
        ("shop.models:Order.save", "shop.models:Order.validate"),
        ("shop.models:Order.save", "shop.models:Base.save"),
        ("shop.models:Order.empty", "shop.models:Order.build"),
        ("shop.models:Order.build", "shop.models:Order.__init__"),
        # module attribute through `from shop import util`
        ("shop.models:Base.save", "shop.util:log"),
        # re-export in shop/__init__.py
        ("main:run", "shop.orders:place_order"),
        # module-level code
        ("main:<module>", "main:run"),
    }
    assert expected <= found
    assert found - expected == set()


def test_unknown_receiver_gives_possible_edges(shop):
    graph = build_graph(shop)
    # order.save(): the type of `order` is not tracked, so both save methods are candidates.
    assert edges(graph, "possible") == {
        ("shop.orders:place_order", "shop.models:Base.save"),
        ("shop.orders:place_order", "shop.models:Order.save"),
    }


def test_too_many_candidates_are_dropped(tmp_path):
    classes = "\n".join(f"class C{i}:\n    def run(self):\n        pass\n" for i in range(4))
    write_project(tmp_path, {"a.py": classes + "\ndef go(x):\n    x.run()\n"})
    graph = build_graph(tmp_path)
    assert graph.edges == []
    assert graph.stats.unresolved == 1


def test_stats(shop):
    stats = build_graph(shop).stats
    assert stats.files == 5
    assert stats.files_with_errors == 0
    # super, print, len
    assert stats.builtin == 3
    # json.dumps
    assert stats.external == 1
    # unknown_function
    assert stats.unresolved == 1
    assert stats.possible == 1
    assert stats.calls == stats.certain + stats.possible + stats.builtin + stats.external + stats.unresolved


def test_inherited_method_and_aliased_import(tmp_path):
    write_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/base.py": """
                class Plugin:
                    def setup(self):
                        pass
            """,
            "pkg/impl.py": """
                from pkg import base as b

                class Mine(b.Plugin):
                    def start(self):
                        self.setup()
            """,
        },
    )
    assert ("pkg.impl:Mine.start", "pkg.base:Plugin.setup") in edges(build_graph(tmp_path))


def test_wildcard_import(tmp_path):
    write_project(
        tmp_path,
        {
            "lib.py": "def tool():\n    pass\n",
            "app.py": "from lib import *\n\ndef go():\n    tool()\n",
        },
    )
    assert edges(build_graph(tmp_path)) == {("app:go", "lib:tool")}


def test_import_cycle_terminates(tmp_path):
    write_project(
        tmp_path,
        {
            "a.py": "from b import thing\n\ndef go():\n    thing()\n",
            "b.py": "from a import thing\n",
        },
    )
    graph = build_graph(tmp_path)
    assert graph.stats.unresolved == 1


def test_class_body_names_are_not_visible_in_methods(tmp_path):
    write_project(
        tmp_path,
        {
            "a.py": """
                def helper():
                    pass

                class K:
                    def helper(self):
                        pass

                    def run(self):
                        helper()
            """,
        },
    )
    assert edges(build_graph(tmp_path)) == {("a:K.run", "a:helper")}


def test_repeated_calls_merge_into_one_edge(tmp_path):
    write_project(tmp_path, {"a.py": "def f():\n    pass\n\ndef g():\n    f()\n    f()\n"})
    (edge,) = build_graph(tmp_path).edges
    assert (edge.source, edge.target, edge.lines) == ("a:g", "a:f", [5, 6])


def test_skips_hidden_and_virtualenv_folders(tmp_path):
    write_project(tmp_path, {"a.py": "", ".venv/lib/x.py": "", "venv/y.py": "", "__pycache__/z.py": ""})
    assert build_graph(tmp_path).stats.files == 1


def test_cli_graph(shop, capsys):
    assert main(["graph", str(shop), "--compact"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"root", "nodes", "edges", "stats"}
    assert data["stats"]["files"] == 5


def test_cli_graph_rejects_file(shop, capsys):
    assert main(["graph", str(shop / "main.py")]) == 2
    assert "not a directory" in capsys.readouterr().err
