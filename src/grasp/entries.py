"""Guess where a reader should start: the places execution enters the project.

Framework handlers (web routes, background tasks, CLI commands) are never
called from project code, so they are found through their decorators. Test
code is left out: it calls everything but explains little.
"""

from __future__ import annotations

import re
import tomllib
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from grasp.graph import GraphEdge, GraphNode

EntryKind = Literal["script", "main", "route", "task", "cli", "reach"]

MAX_BY_REACH = 15

# Matched against a decorator's callee, e.g. "router.post" in @router.post("/login").
_DECORATOR_KINDS: list[tuple[EntryKind, re.Pattern[str]]] = [
    ("route", re.compile(r"\.(get|post|put|patch|delete|head|options|route|api_route|websocket"
                         r"|on_event|exception_handler|middleware)$")),
    ("task", re.compile(r"(^|\.)(task|shared_task|periodic_task|actor)$")),
    ("cli", re.compile(r"(^|\.)(command|group|callback)$")),
]

@dataclass(frozen=True)
class EntryPoint:
    id: str
    kind: EntryKind
    reach: int # how many nodes are reachable through certain calls

def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    if any(part in ("tests", "test") for part in p.parts[:-1]):
        return True
    return p.name == "conftest.py" or p.name.startswith("test_") or p.stem.endswith("_test")

def project_scripts(root: Path) -> list[str]:
    """Node ids named by `[project.scripts]` in pyproject.toml, e.g. "pkg.cli:main"."""
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    scripts = data.get("project", {}).get("scripts", {})
    return [target.split("[")[0].strip() for target in scripts.values()]

def find_entry_points(nodes: list[GraphNode], edges: list[GraphEdge], scripts: list[str]) -> list[EntryPoint]:
    by_id = {n.id: n for n in nodes}
    called: set[str] = set() # called from non-test code, possibly
    callees: dict[str, list[str]] = {} # certain calls only
    for e in edges:
        if not by_id[e.source].is_test:
            called.add(e.target)
        if e.confidence == "certain":
            callees.setdefault(e.source, []).append(e.target)
    
    def reach(start: str) -> int:
        seen, queue = {start}, deque([start])
        while queue:
            for target in callees.get(queue.popleft(), []):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        return len(seen) - 1
    
    found: dict[str, EntryKind] = {}

    def add(node_id: str, kind: EntryKind) -> None:
        node = by_id.get(node_id)
        if node is not None and not node.is_test and node_id not in found:
            found[node.id] = kind
    
    for node_id in scripts:
        add(node_id, "script")
    for n in nodes:
        if n.kind == "module" and n.is_main:
            add(n.id, "main")
    for n in nodes:
        kind = next((k for d in n.decorators if (k := _decorator_kind(d))), None)
        if kind is not None:
            add(n.id, kind)
    
    # Anything else nobody calls, ranked by how much of the project it drives.
    candidates = [
        n for n in nodes
        if n.id not in found and n.id not in called and not n.is_test and n.kind != "class" and n.id in callees
    ]
    reaches = {n.id: reach(n.id) for n in candidates}
    ranked = sorted(candidates, key=lambda n: (-reaches[n.id], n.id))[:MAX_BY_REACH]

    return [EntryPoint(node_id, kind, reach(node_id)) for node_id, kind in found.items()] + [
        EntryPoint(n.id, "reach", reaches[n.id]) for n in ranked
    ]


def _decorator_kind(decorator: str) -> EntryKind | None:
    head = decorator.split("(", 1)[0].replace(" ", "")
    for kind, pattern in _DECORATOR_KINDS:
        if pattern.search(head):
            return kind
    return None