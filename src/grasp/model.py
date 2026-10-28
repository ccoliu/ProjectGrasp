"""Language-neutral results produced by a parser for a single file.

Positions are 1-based lines and 1-based columns counted in characters,
so they can be pasted straight into an editor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

MODULE_SCOPE = "<module>"

DefinitionKind = Literal["function", "method", "class"]
CalleeKind = Literal["name", "attribute", "other"]


@dataclass(frozen=True)
class Definition:
    name: str
    qualname: str
    kind: DefinitionKind
    parent: str | None
    line: int
    column: int
    end_line: int
    is_async: bool = False
    decorators: tuple[str, ...] = ()


@dataclass(frozen=True)
class CallSite:
    """A call expression and the scope it appears in.

    `callee` is the called name (`foo` in `foo()` or `obj.foo()`), or None when
    the callee is not a plain name, e.g. `handlers[0]()`. `receiver` is the
    source text left of the final dot for attribute calls. `text` is the
    source of the whole callee expression, without the arguments.
    """

    caller: str
    callee: str | None
    receiver: str | None
    kind: CalleeKind
    text: str
    line: int
    column: int


@dataclass
class FileResult:
    path: str
    language: str
    has_errors: bool = False
    definitions: list[Definition] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
