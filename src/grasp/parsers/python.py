"""Python parser built on tree-sitter.

Qualified names follow Python's `__qualname__`: `Cls.method`,
`outer.<locals>.inner`. Calls are attributed to the scope that evaluates them,
so decorator arguments, default values and base classes belong to the
enclosing scope rather than the function or class being defined.
"""

from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from grasp.model import MODULE_SCOPE, CallSite, Definition, FileResult
from grasp.parsers.base import LanguageParser

_LANGUAGE = Language(tree_sitter_python.language())


@dataclass(frozen=True)
class _Scope:
    qualname: str
    kind: str  # "module" | "class" | "function"

    def child_qualname(self, name: str) -> str:
        if self.kind == "module":
            return name
        if self.kind == "function":
            return f"{self.qualname}.<locals>.{name}"
        return f"{self.qualname}.{name}"


_MODULE = _Scope(MODULE_SCOPE, "module")


class PythonParser(LanguageParser):
    language = "python"
    extensions = (".py", ".pyi")

    def __init__(self) -> None:
        self._parser = Parser(_LANGUAGE)

    def parse(self, source: bytes, path: str) -> FileResult:
        tree = self._parser.parse(source)
        result = FileResult(path=path, language=self.language, has_errors=tree.root_node.has_error)
        _Extractor(source, result).run(tree.root_node)
        result.definitions.sort(key=lambda d: (d.line, d.column))
        result.calls.sort(key=lambda c: (c.line, c.column))
        return result


class _Extractor:
    def __init__(self, source: bytes, result: FileResult) -> None:
        self._source = source
        self._result = result

    def run(self, root: Node) -> None:
        # Iterative walk: deeply nested code must not hit the recursion limit.
        stack: list[tuple[Node, _Scope]] = [(root, _MODULE)]
        while stack:
            node, scope = stack.pop()
            if node.type in ("function_definition", "class_definition"):
                inner = self._define(node, scope)
                if inner is not None:
                    body = node.child_by_field_name("body")
                    for child in node.named_children:
                        stack.append((child, inner if child == body else scope))
                    continue
            elif node.type == "call":
                self._record_call(node, scope)
            stack.extend((child, scope) for child in node.named_children)

    def _define(self, node: Node, scope: _Scope) -> _Scope | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # error recovery can produce a nameless definition
            return None
        name = _text(name_node)
        qualname = scope.child_qualname(name)
        if node.type == "class_definition":
            kind, inner = "class", _Scope(qualname, "class")
        else:
            kind = "method" if scope.kind == "class" else "function"
            inner = _Scope(qualname, "function")

        self._result.definitions.append(
            Definition(
                name=name,
                qualname=qualname,
                kind=kind,
                parent=None if scope is _MODULE else scope.qualname,
                line=node.start_point.row + 1,
                column=self._column(node),
                end_line=node.end_point.row + 1,
                is_async=any(child.type == "async" for child in node.children),
                decorators=_decorators(node),
            )
        )
        return inner

    def _record_call(self, node: Node, scope: _Scope) -> None:
        func = node.child_by_field_name("function")
        if func is None:
            return
        callee: str | None = None
        receiver: str | None = None
        if func.type == "identifier":
            kind, callee = "name", _text(func)
        elif func.type == "attribute":
            kind = "attribute"
            attr = func.child_by_field_name("attribute")
            obj = func.child_by_field_name("object")
            callee = _text(attr) if attr is not None else None
            receiver = _text(obj) if obj is not None else None
        else:
            kind = "other"

        self._result.calls.append(
            CallSite(
                caller=scope.qualname,
                callee=callee,
                receiver=receiver,
                kind=kind,
                text=_text(func),
                line=node.start_point.row + 1,
                column=self._column(node),
            )
        )

    def _column(self, node: Node) -> int:
        # tree-sitter columns are byte offsets; convert to characters.
        start = node.start_byte
        line_start = self._source.rfind(b"\n", 0, start) + 1
        return len(self._source[line_start:start].decode("utf-8", errors="replace")) + 1


def _text(node: Node) -> str:
    return " ".join(node.text.decode("utf-8", errors="replace").split())


def _decorators(node: Node) -> tuple[str, ...]:
    parent = node.parent
    if parent is None or parent.type != "decorated_definition":
        return ()
    return tuple(
        _text(child.named_children[0])
        for child in parent.named_children
        if child.type == "decorator" and child.named_children
    )
