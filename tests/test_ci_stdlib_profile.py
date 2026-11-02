"""Keep the archive's small test group separate without dropping its plugins."""

from importlib.metadata import entry_points
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_archive_group_keeps_runner_and_existing_plugins() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    group = project["dependency-groups"]["ci-stdlib"]
    names = {re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0] for requirement in group}
    assert names == {"pytest", "pytest-asyncio", "anyio", "platformdirs"}
    assert "ci-stdlib" not in project["tool"]["uv"].get("default-groups", ["dev"])
    # The complete test extra/server dependencies remain independently available.
    assert {"fastmcp", "mcp", "pydantic"} <= {
        re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0]
        for requirement in project["project"]["dependencies"]
    }
    assert "test" in project["project"]["optional-dependencies"]


def test_existing_pytest_plugins_are_available() -> None:
    plugins = {entry.value for entry in entry_points(group="pytest11")}
    assert {"pytest_asyncio.plugin", "anyio.pytest_plugin", "platformdirs.pytest_plugin"} <= plugins


def test_archive_workflow_selects_only_group_without_shadowing_source() -> None:
    workflow = (ROOT / ".github/workflows/jsonl-archive.yml").read_text(encoding="utf-8")
    assert "uv run --locked --only-group ci-stdlib --exact pytest -q tests/test_jsonl_log_archive.py" in workflow
    assert "PYTHONPATH: src" in workflow
    assert "uv run --no-project python -S src/mcp_toolcall_lab/jsonl_log_archive.py --help" in workflow
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in workflow
    # Both PR and main-push path filters must exercise future group/lock changes.
    for path in ("pyproject.toml", "uv.lock", "tests/test_ci_stdlib_profile.py"):
        assert workflow.count(f"      - {path}\n") == 2
