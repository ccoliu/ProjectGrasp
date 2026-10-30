import json
import re

from grasp.cli import main
from grasp.graph import CallGraph, GraphNode, Stats, build_graph
from grasp.viewer import render_html


def embedded(page: str, name: str):
    match = re.search(rf"const {name} = (.*?);\n", page)
    assert match, f"{name} not found in page"
    return json.loads(match.group(1).replace("<\\/", "</"))


def embedded_data(page: str) -> dict:
    return embedded(page, "DATA")

def test_page_embeds_graph(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    g()\n\ndef g():\n    pass\n", encoding="utf-8")
    graph = build_graph(tmp_path)
    page = render_html(graph)
    assert "__GRASP_DATA__" not in page
    data = embedded_data(page)
    assert data["edges"] == [{"source": "a:f", "target": "a:g", "confidence": "certain", "lines": [2]}]
    assert {n["id"] for n in data["nodes"]} == {"a:<module>", "a:f", "a:g"}


def test_script_end_tag_in_data_is_escaped():
    node = GraphNode("m:f", "m", "f", "function", "dir</script>/m.py", 1, 1)
    page = render_html(CallGraph(root="x", nodes=[node], edges=[], stats=Stats()))
    assert page.count("</script>") == page.count("<script")
    assert embedded_data(page)["nodes"][0]["path"] == "dir</script>/m.py"


def test_cli_view_writes_html(tmp_path, capsys):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    out = tmp_path / "out.html"
    assert main(["view", str(tmp_path / "proj"), "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert "wrote" in capsys.readouterr().out


def test_cli_view_rejects_file(tmp_path, capsys):
    path = tmp_path / "a.py"
    path.write_text("")
    assert main(["view", str(path)]) == 2
    assert "not a directory" in capsys.readouterr().err

def test_page_embeds_sources(tmp_path):
    code = "def f():\n    return '</script>'\n"
    (tmp_path / "a.py").write_text(code, encoding="utf-8")
    page = render_html(build_graph(tmp_path))
    assert embedded(page, "SOURCES") == {"a.py": code}
    assert page.count("</script>") == page.count("<script")
