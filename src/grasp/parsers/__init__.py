from __future__ import annotations

from pathlib import Path

from grasp.parsers.base import LanguageParser
from grasp.parsers.python import PythonParser

_PARSERS: list[LanguageParser] = [PythonParser()]


def parser_for(path: str | Path) -> LanguageParser | None:
    suffix = Path(path).suffix.lower()
    for parser in _PARSERS:
        if suffix in parser.extensions:
            return parser
    return None


__all__ = ["LanguageParser", "PythonParser", "parser_for"]
