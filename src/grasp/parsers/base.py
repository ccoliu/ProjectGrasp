from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from grasp.model import FileResult


class LanguageParser(ABC):
    """Extracts definitions and call sites from one source file.

    Each supported language implements this; nothing downstream (symbol
    resolution, graph building) depends on a specific grammar.
    """

    language: str
    extensions: tuple[str, ...]

    @abstractmethod
    def parse(self, source: bytes, path: str) -> FileResult: ...

    def parse_file(self, path: str | Path) -> FileResult:
        path = Path(path)
        return self.parse(path.read_bytes(), str(path))
