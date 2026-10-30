"""Cross-file symbol resolution: turn per-file parse results into a call graph.

A call becomes a `certain` edge when its callee can be followed through
definitions, imports, `self`/`cls`/`super()` and base classes. A method call on
a receiver of unknown type becomes `possible` edges to every project method
with that name, as long as there are few enough candidates to be useful.
Assignments and type annotations are not tracked.
"""

from __future__ import annotations

import builtins
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from grasp.entries import EntryPoint, find_entry_points, is_test, project_scripts
from grasp.model import MODULE_SCOPE, CallSite, Definition, FileResult, Import
from grasp.parsers import PythonParser

Confidence = Literal["certain", "possible"]

MAX_POSSIBLE_TARGETS = 3
_MAX_DEPTH = 20  # bounds import and inheritance chains, which may be cyclic
_SKIP_DIRS = {"__pycache__", "node_modules", "site-packages", "venv"}
_BUILTINS = frozenset(dir(builtins))

@dataclass(frozen=True)
class GraphNode:
    id: str
    module: str
    qualname: str
    kind: str # "module" | "function" | "method" | "class"
    path: str
    line: int
    end_line: int
    decorators: tuple[str, ...] = ()
    params: tuple[str, ...] = ()
    returns: str | None = None
    is_test: bool = False
    is_main: bool = False  # module code of a file that runs as a program

@dataclass
class GraphEdge:
    source: str
    target: str
    confidence: Confidence
    lines: list[int]

@dataclass
class Stats:
    """Counts per call site. Every call lands in exactly one outcome bucket."""

    files: int = 0
    files_with_errors: int = 0
    calls: int = 0
    certain: int = 0
    possible: int = 0
    builtin: int = 0
    external: int = 0
    unresolved: int = 0

@dataclass
class CallGraph:
    root: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    stats: Stats
    entry_points: list[EntryPoint] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

def build_graph(root: str | Path) -> CallGraph:
    root = Path(root)
    parser = PythonParser()
    modules: dict[str, _Module] = {}
    stats = Stats()

    for path in discover(root):
        name, is_package = module_name(path)
        if name in modules: # same-named top-level scripts in different folders: first wins
            continue
        source = path.read_bytes()
        result = parser.parse(source, path.relative_to(root).as_posix())
        modules[name] = _Module(name, is_package, result, source.count(b"\n") + 1)
        stats.files += 1
        stats.files_with_errors += result.has_errors
    graph = _Resolver(modules, stats).build(root)
    graph.entry_points = find_entry_points(graph.nodes, graph.edges, project_scripts(root))
    return graph

def discover(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filesnames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS)
        files.extend(Path(dirpath, f) for f in sorted(filesnames) if f.endswith(".py"))
    return files

def module_name(path: Path) -> tuple[str, bool]:
    is_package = path.name == "__init__.py"
    parts = [] if is_package else [path.stem]
    folder = path.parent.resolve()
    while (folder / "__init__.py").is_file() and folder.parent != folder:
        parts.append(folder.name)
        folder = folder.parent
    return ".".join(reversed(parts)), is_package

@dataclass
class _Module:
    name: str
    is_package: bool
    result: FileResult
    line_count: int
    defs: dict[tuple[str, str], Definition] = field(default_factory=dict) # (scope, name)
    by_qualname: dict[str, Definition] = field(default_factory=dict)
    imports: dict[tuple[str, str], Import] = field(default_factory=dict) #(scope, bound_name)
    wildcards: list[Import] = field(default_factory=list)

    def __post_init__(self) -> None:
        for d in self.result.definitions:
            self.defs[(d.parent or MODULE_SCOPE, d.name)] = d
            self.by_qualname[d.qualname] = d
        for imp in self.result.imports:
            if imp.name == "*":
                self.wildcards.append(imp)
            else:
                self.imports[(imp.scope, imp.bound_name)] = imp
    
    def node_id(self, qualname: str) -> str:
        return f"{self.name}:{qualname}"

# What a name resolves to.
@dataclass(frozen=True)
class _Def:
    module: _Module
    definition: Definition


@dataclass(frozen=True)
class _ModuleRef:
    module: _Module


@dataclass(frozen=True)
class _External:
    name: str


_Binding = _Def | _ModuleRef | _External


class _Resolver:
    def __init__(self, modules: dict[str, _Module], stats: Stats) -> None:
        self._modules = modules
        self._stats = stats
        self._methods_by_name: dict[str, list[_Def]] = {}
        for m in modules.values():
            for d in m.result.definitions:
                if d.kind == "method":
                    self._methods_by_name.setdefault(d.name, []).append(_Def(m, d))

    def build(self, root: Path) -> CallGraph:
        nodes: list[GraphNode] = []
        edges: dict[tuple[str, str], GraphEdge] = {}
        for m in self._modules.values():
            path = m.result.path
            test = is_test(path)
            runs_as_program = m.result.has_main_guard or m.name.split(".")[-1] == "__main__"
            nodes.append(GraphNode(
                m.node_id(MODULE_SCOPE), m.name, MODULE_SCOPE, "module", path, 1, m.line_count,
                is_test=test, is_main=runs_as_program,
            ))
            for d in m.result.definitions:
                nodes.append(GraphNode(
                    m.node_id(d.qualname), m.name, d.qualname, d.kind, path, d.line, d.end_line,
                    decorators=d.decorators, params=d.params, returns=d.returns, is_test=test,
                ))
        
            for call in m.result.calls:
                outcome, targets = self._resolve_call(m, call)
                self._stats.calls += 1
                setattr(self._stats, outcome, getattr(self._stats, outcome) + 1)
                for target in targets:
                    self._add_edge(edges, m.node_id(call.caller), target, outcome, call.line)

        return CallGraph(
            root=root.as_posix(),
            nodes=nodes,
            edges=sorted(edges.values(), key=lambda e: (e.source, e.target)),
            stats=self._stats,
        )

    @staticmethod
    def _add_edge(edges, source: str, target: str, confidence: Confidence, line: int) -> None:
        edge = edges.get((source, target))
        if edge is None:
            edges[(source, target)] = GraphEdge(source, target, confidence, [line])
            return
        edge.lines.append(line)
        if confidence == "certain":
            edge.confidence = "certain"
    
    # -- calls --------------------------------------------------------------

    def _resolve_call(self, m: _Module, call: CallSite) -> tuple[str, list[str]]:
        """Returns the outcome (a Stats bucket) and the target node ids."""
        if call.kind == "name":
            binding = self._lookup(m, call.caller, call.callee)
            if binding is None:
                return ("builtin" if call.callee in _BUILTINS else "unresolved"), []
            return self._call_target(binding)

        if call.kind == "attribute" and call.callee is not None:
            cls = self._enclosing_class(m, call.caller)
            if cls is not None and call.receiver in ("self", "cls"):
                found = self._find_method(m, cls, call.callee)
                if found is not None:
                    return self._call_target(found)
            elif cls is not None and call.receiver == "super()":
                found = self._find_in_bases(m, cls, call.callee)
                if found is not None:
                    return self._call_target(found)
            elif _is_dotted(call.receiver):
                receiver = self._resolved_dotted(m, call.caller, call.receiver)
                if receiver is not None:
                    member = self._member(receiver, call.callee)
                    if member is not None:
                        return self._call_target(member)
                    return ("external" if isinstance(receiver, _External) else "unresolved"), []
        
            candidates = self._methods_by_name.get(call.callee, [])
            if 0 < len(candidates) <= MAX_POSSIBLE_TARGETS:
                return "possible", [c.module.node_id(c.definition.qualname) for c in candidates]
        
        return "unresolved", []
    
    def _call_target(self, binding: _Binding) -> tuple[str, list[str]]:
        if isinstance(binding, _External):
            return "external", []
        if isinstance(binding, _ModuleRef):
            return "unresolved", []
        target = binding
        if binding.definition.kind == "class": #constructor call
            target = self._find_method(binding.module, binding.definition, "__init__") or binding
        return "certain", [target.module.node_id(target.definition.qualname)    ]

    # -- names --------------------------------------------------------------

    def _lookup(self, m: _Module, scope: str, name: str, depth: int = 0) -> _Binding | None:
        """Python's LEGB lookup: enclosing class bodies are skipped, except the
        scope the name appears in."""
        current, first = scope, True
        while True:
            definition = m.by_qualname.get(current)
            if first or definition is None or definition.kind != "class":
                found = self._lookup_in_scope(m, current, name, depth)
                if found is not None:
                    return found
            if definition is None:
                return None
            current, first = definition.parent or MODULE_SCOPE, False
    
    def _lookup_in_scope(self, m: _Module, scope: str, name: str, depth: int) -> _Binding | None:
        if depth > _MAX_DEPTH:
            return None
        definition = m.defs.get((scope, name))
        if definition is not None:
            return _Def(m, definition)
        imp = m.imports.get((scope, name))
        if imp is not None:
            return self._follow_import(m, imp, depth + 1)
        if scope == MODULE_SCOPE:
            for wildcard in m.wildcards:
                target = self._module_ref(self._absolute_module(m, wildcard))
                if isinstance(target, _ModuleRef):
                    found = self._member(target, name, depth + 1)
                    if isinstance(found, (_Def, _ModuleRef)):
                        return found
        return None

    def _follow_import(self, m: _Module, imp: Import, depth: int) -> _Binding | None:
        target = self._absolute_module(m, imp)
        if target is None:
            return None
        if imp.name is None:
            # `import a.b` binds `a`; `import a.b as c` binds `a.b`.
            return self._module_ref(target if imp.alias else target.split(".")[0])
        return self._member(self._module_ref(target), imp.name, depth + 1)
    
    def _absolute_module(self, m: _Module, imp: Import) -> str | None:
        if imp.level == 0:
            return imp.module
        package = m.name if m.is_package else m.name.rpartition(".")[0]
        parts = package.split(".") if package else []
        if imp.level - 1 > len(parts): # climbs above the top-level package
            return None
        parts = parts[: len(parts) - (imp.level - 1)]
        if imp.module:
            parts.append(imp.module)
        return ".".join(parts)
    
    def _module_ref(self, name: str | None) -> _Binding | None:
        if name is None:
            return None
        module = self._modules.get(name)
        return _ModuleRef(module) if module is not None else _External(name)
    
    def _member(self, binding: _Binding | None, attr: str, depth: int = 0) -> _Binding | None:
        if binding is None or depth > _MAX_DEPTH:
            return None
        if isinstance(binding, _External): # 屬於外部模組
            return _External(f"{binding.name}.{attr}")
        if isinstance(binding, _ModuleRef): # 該專案的其他檔案參照
            module = binding.module
            found = self._lookup_in_scope(module, MODULE_SCOPE, attr, depth + 1)
            if found is not None:
                return found
            submodule = self._modules.get(f"{module.name}.{attr}")
            return _ModuleRef(submodule) if submodule is not None else None
        if binding.definition.kind == "class":
            return self._find_method(binding.module, binding.definition, attr, depth + 1)
        return None

    def _resolved_dotted(self, m: _Module, scope: str, text: str, depth: int = 0) -> _Binding | None:
        head, *rest = text.split(".")
        binding = self._lookup(m, scope, head, depth)
        for part in rest:
            binding = self._member(binding, part, depth)
        return binding

    # -- classes ------------------------------------------------------------

    def _enclosing_class(self, m: _Module, scope: str) -> Definition | None:
        definition = m.by_qualname.get(scope)
        while definition is not None and definition.kind != "class":
            definition = m.by_qualname.get(definition.parent) if definition.parent else None
        return definition

    def _find_method(self, m: _Module, cls: Definition, name: str, depth: int = 0) -> _Def | None:
        definition = m.defs.get((cls.qualname, name))
        if definition is not None:
            return _Def(m, definition)
        return self._find_in_bases(m, cls, name, depth)

    def _find_in_bases(self, m: _Module, cls: Definition, name: str, depth: int = 0) -> _Def | None:
        if depth > _MAX_DEPTH:
            return None
        for base in cls.bases:
            if not _is_dotted(base):
                continue
            resolved = self._resolved_dotted(m, cls.parent or MODULE_SCOPE, base, depth + 1)
            if isinstance(resolved, _Def) and resolved.definition.kind == "class":
                found = self._find_method(resolved.module, resolved.definition, name, depth + 1)
                if found is not None:
                    return found
        return None
    
def _is_dotted(text: str | None) -> bool:
    return bool(text) and all(part.isidentifier() for part in text.split("."))
