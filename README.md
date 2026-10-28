# ProjectGrasp

Build a mental map of an unfamiliar codebase fast: point it at source code, get a function call graph you can explore.

ProjectGrasp deliberately skips deep semantic analysis. It only extracts function definitions and call sites with [tree-sitter](https://tree-sitter.github.io/), which keeps it fast and tolerant of syntax errors. The trade-off is that some call edges can only be marked as "possible".

## Status

MVP stage 1: single-file parsing for Python.

## Usage

```sh
uv sync
uv run grasp parse path/to/module.py
```

The output is JSON with two lists:

- `definitions`: functions, methods and classes, with qualified names following Python's `__qualname__` (`Cls.method`, `outer.<locals>.inner`).
- `calls`: every call expression, the scope that evaluates it (`caller`, or `<module>` at top level), the called name (`callee`) and, for `obj.method()`, the `receiver` text.

Lines and columns are 1-based; columns count characters.

## Development

```sh
uv run pytest
```
