"""Render a call graph as one self-contained, interactive HTML page.

The page embeds the source of every file in the graph so the viewer can show
code next to the diagram. Treat the output like the source itself when sharing.
"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from grasp.graph import CallGraph

_DATA = "/*__GRASP_DATA__*/null"
_SOURCES = "/*__GRASP_SOURCES__*/null"


def render_html(graph: CallGraph) -> str:
    template = files("grasp").joinpath("viewer.html").read_text(encoding="utf-8")
    page = template.replace(_DATA, _to_script(graph.to_dict()), 1)
    return page.replace(_SOURCES, _to_script(_read_sources(graph)), 1)


def _read_sources(graph: CallGraph) -> dict[str, str]:
    root = Path(graph.root)
    sources: dict[str, str] = {}
    for path in sorted({n.path for n in graph.nodes}):
        try:
            sources[path] = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass  # the viewer says "source not available"
    return sources


def _to_script(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    # The JSON sits inside a <script> element: a literal "</script>" in any
    # string (a file path, say) would end the element early.
    return data.replace("</", "<\\/")
