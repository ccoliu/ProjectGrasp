from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from grasp import __version__
from grasp.graph import build_graph
from grasp.parsers import parser_for
from grasp.viewer import render_html


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grasp", description="Function call graphs for reading code.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="list definitions and call sites in one file as JSON")
    parse_cmd.add_argument("file", type=Path)
    parse_cmd.add_argument("--compact", action="store_true", help="print JSON on one line")

    graph_cmd = sub.add_parser("graph", help="build the call graph of a Python project as JSON")
    graph_cmd.add_argument("root", type=Path)
    graph_cmd.add_argument("--compact", action="store_true", help="print JSON on one line")

    view_cmd = sub.add_parser("view", help="write an interactive call graph page (HTML)")
    view_cmd.add_argument("root", type=Path)
    view_cmd.add_argument("-o", "--output", type=Path, default=Path("grasp.html"), help="default: grasp.html")
    view_cmd.add_argument("--open", action="store_true", help="open the page in a browser")

    args = ap.parse_args(argv)
    if args.command == "graph":
        return _graph(args.root, args.compact)
    if args.command == "view":
        return _view(args.root, args.output, args.open)
    return _parse(args.file, args.compact)


def _parse(path: Path, compact: bool) -> int:
    if not path.is_file():
        print(f"grasp: not a file: {path}", file=sys.stderr)
        return 2
    parser = parser_for(path)
    if parser is None:
        print(f"grasp: unsupported file type: {path.suffix or path.name}", file=sys.stderr)
        return 2

    _emit(parser.parse_file(path).to_dict(), compact)
    return 0

def _graph(root: Path, compact: bool) -> int:
    if not root.is_dir():
        print(f"grasp: not a directory: {root}", file=sys.stderr)
        return 2

    _emit(build_graph(root).to_dict(), compact)
    return 0

def _view(root: Path, output: Path, open_browser: bool) -> int:
    if not root.is_dir():
        print(f"grasp: not a directory: {root}", file=sys.stderr)
        return 2

    graph = build_graph(root)
    output.write_text(render_html(graph), encoding="utf-8")
    print(f"wrote {output} ({len(graph.nodes)} nodes, {len(graph.edges)} edges)")
    if open_browser:
        webbrowser.open(output.resolve().as_uri())
    return 0

def _emit(data: dict, compact: bool) -> None:
    # Windows consoles default to a legacy code page; identifiers may be non-ASCII.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    json.dump(data, sys.stdout, ensure_ascii=False, indent=None if compact else 2)
    sys.stdout.write("\n")
