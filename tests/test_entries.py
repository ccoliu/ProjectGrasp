import textwrap

from grasp.entries import is_test
from grasp.graph import build_graph


def write_project(root, files: dict[str, str]):
    for rel, code in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(code), encoding="utf-8")
    return root


def entries(root):
    return {e.id: e.kind for e in build_graph(root).entry_points}


def test_is_test():
    assert is_test("tests/test_api.py")
    assert is_test("pkg/tests/helpers.py")
    assert is_test("conftest.py")
    assert is_test("pkg/api_test.py")
    assert not is_test("pkg/testing.py")
    assert not is_test("app/api/tasks.py")


def test_framework_handlers_are_entry_points(tmp_path):
    write_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/api.py": """
                @router.post("/login", response_model=Token)
                def login(payload: LoginForm) -> Token:
                    return issue(payload)

                def issue(payload):
                    pass
            """,
            "app/jobs.py": """
                @celery_app.task(bind=True)
                def run_job(self, job_id: int):
                    pass

                @shared_task
                def cleanup():
                    pass
            """,
            "app/cli.py": """
                @click.command()
                def serve():
                    pass
            """,
        },
    )
    assert entries(tmp_path) == {
        "app.api:login": "route",
        "app.jobs:run_job": "task",
        "app.jobs:cleanup": "task",
        "app.cli:serve": "cli",
    }


def test_scripts_and_main_guard(tmp_path):
    write_project(
        tmp_path,
        {
            "pyproject.toml": """
                [project]
                name = "tool"
                [project.scripts]
                tool = "tool.cli:main"
            """,
            "tool/__init__.py": "",
            "tool/cli.py": "def main():\n    pass\n",
            "tool/__main__.py": "from tool.cli import main\n\nmain()\n",
            "run.py": "def go():\n    pass\n\nif __name__ == '__main__':\n    go()\n",
        },
    )
    assert entries(tmp_path) == {
        "tool.cli:main": "script",
        "tool.__main__:<module>": "main",
        "run:<module>": "main",
    }


def test_tests_are_excluded_and_rest_ranked_by_reach(tmp_path):
    write_project(
        tmp_path,
        {
            "lib.py": """
                def a():
                    b()

                def b():
                    c()

                def c():
                    pass

                def small():
                    c()
            """,
            "tests/test_lib.py": """
                from lib import a, small

                def test_everything():
                    a()
                    small()
            """,
        },
    )
    graph = build_graph(tmp_path)
    assert [(e.id, e.kind, e.reach) for e in graph.entry_points] == [
        ("lib:a", "reach", 2),
        ("lib:small", "reach", 1),
    ]
    test_node = next(n for n in graph.nodes if n.id == "test_lib:test_everything")
    assert test_node.is_test
