# ProjectGrasp

Build a mental map of an unfamiliar codebase fast: point it at source code, get a function call graph you can explore.

ProjectGrasp deliberately skips deep semantic analysis. It only extracts function definitions and call sites with [tree-sitter](https://tree-sitter.github.io/), which keeps it fast and tolerant of syntax errors. The trade-off is that some call edges can only be marked as "possible".

## Status

MVP stage 3: interactive call graph page for Python projects.

## Usage

```sh
uv sync
uv run grasp view path/to/project --open   # interactive page, written to grasp.html
uv run grasp graph path/to/project         # call graph as JSON
uv run grasp parse path/to/module.py       # one file's definitions and calls as JSON
```

In the page, pick an entry point or search for a function, then double-click nodes to expand what they call. Entry points are found from `[project.scripts]`, `if __name__ == "__main__"`, and framework decorators (web routes, Celery tasks, CLI commands); tests are hidden by default. Selecting a node shows its source, and the arrows around it are labelled with the callee's annotated parameter and return types. Solid edges are certain calls, dashed edges are possible ones. Add `#focus=<module>:<qualname>` to the URL to open on a given function. The page loads Cytoscape.js and highlight.js from a CDN, so it needs an internet connection. It also embeds the project's source code: share it only where the code itself may go.


`grasp parse` prints JSON with three lists:

- `definitions`: functions, methods and classes, with qualified names following Python's `__qualname__` (`Cls.method`, `outer.<locals>.inner`).
- `calls`: every call expression, the scope that evaluates it (`caller`, or `<module>` at top level), the called name (`callee`) and, for `obj.method()`, the `receiver` text.
- `imports`: each imported name and the scope it is bound in.

Lines and columns are 1-based; columns count characters.

## Development

```sh
uv run pytest
```
