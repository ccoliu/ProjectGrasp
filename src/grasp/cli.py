from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grasp import __version__
from grasp.parsers import parser_for


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grasp", description="Function call graphs for reading code.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="list definitions and call sites in one file as JSON")
    parse_cmd.add_argument("file", type=Path)
    parse_cmd.add_argument("--compact", action="store_true", help="print JSON on one line")

    args = ap.parse_args(argv)
    return _parse(args.file, args.compact)


def _parse(path: Path, compact: bool) -> int:
    if not path.is_file():
        print(f"grasp: not a file: {path}", file=sys.stderr)
        return 2
    parser = parser_for(path)
    if parser is None:
        print(f"grasp: unsupported file type: {path.suffix or path.name}", file=sys.stderr)
        return 2

    result = parser.parse_file(path)
    # Windows consoles default to a legacy code page; identifiers may be non-ASCII.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    json.dump(result.to_dict(), sys.stdout, ensure_ascii=False, indent=None if compact else 2)
    sys.stdout.write("\n")
    return 0
