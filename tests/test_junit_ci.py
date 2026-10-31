"""Exercise real pytest JUnit production without changing child outcomes.

The shared pinned collector is exercised by Actions; this regression uses no
runtime dependencies and never imports a repository-local JUnit collector.
"""
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml


@pytest.mark.parametrize("failing", [False, True])
def test_pytest_junit_preserves_outcome(tmp_path, failing):
    source = tmp_path / "test_child.py"
    if failing:
        source.write_text(
            "import pytest\n"
            "def test_failure():\n    assert False, 'private assertion detail'\n"
            "@pytest.fixture\ndef broken():\n    raise RuntimeError('private setup detail')\n"
            "def test_setup_error(broken):\n    pass\n"
            "def test_skip():\n    pytest.skip('private skip detail')\n",
            encoding="utf-8",
        )
    else:
        source.write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    command = [sys.executable, "-m", "pytest", "-q", "-c", os.devnull, "--rootdir", str(tmp_path), str(source)]
    plain = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=30)
    report = tmp_path / "reports" / "pytest.xml"
    reported = subprocess.run(
        command + ["--junitxml=" + str(report)],
        cwd=tmp_path, env=env, capture_output=True, timeout=30,
    )
    assert plain.returncode == reported.returncode == (1 if failing else 0)
    cases = ET.parse(report).getroot().findall(".//testcase")
    assert {case.attrib["classname"] for case in cases} == {"test_child"}
    assert len(cases) == (3 if failing else 1)
    assert len([case for case in cases if case.find("failure") is not None]) == int(failing)
    assert len([case for case in cases if case.find("error") is not None]) == int(failing)
    assert len([case for case in cases if case.find("skipped") is not None]) == int(failing)


def test_python_junit_workflow_keeps_failed_producer_visible():
    workflow = (Path(__file__).parents[1] / ".github/workflows/test.yml").read_text(encoding="utf-8")
    producer = workflow.split("  test:\n", 1)[1].split("  junit-identity:\n", 1)[0]
    collector = workflow.split("  junit-identity:\n", 1)[1].split("  public-resolver-compat:", 1)[0]
    assert "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3" in producer
    assert "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0" in producer
    assert "node-version: ${{ matrix.node-version }}" in producer
    assert "run: uv run --locked --extra test pytest -q --junitxml=reports/pytest.xml" in producer
    assert "continue-on-error" not in producer
    assert "|| true" not in producer
    assert "if: always()" in producer
    assert "name: junit-python-${{ matrix.python-version }}-node-${{ matrix.node-version }}" in producer
    assert "path: reports/pytest.xml" in producer
    assert "if-no-files-found: error" in producer
    assert "needs: [changes, test]" in collector
    assert "if: always() && needs.changes.outputs.product_changed == 'true'" in collector
    assert "reusable-junit-identity.yml@9e3da7ae1846e9604ebc24bb5178f7de094a4e77" in collector
    assert "commit-sha: ${{ github.event.pull_request.head.sha || github.sha }}" in collector
    assert 'expected-reports: \'["junit-python-3.12.15-node-24.22.1/pytest.xml", "junit-python-3.12.15-node-26.12.1/pytest.xml", "junit-python-3.15-node-26.12.1/pytest.xml"]\'' in collector
    assert "artifact-pattern: junit-python-*-node-*" in collector
    assert "actions: read" in collector
    assert "pages:" not in collector


def test_public_resolver_compat_keeps_legacy_python_coverage():
    workflow = (Path(__file__).parents[1] / ".github/workflows/test.yml").read_text(encoding="utf-8")
    resolver = yaml.safe_load(workflow)["jobs"]["public-resolver-compat"]
    # uv provides Python 3.9 on the current runner image.
    assert resolver["runs-on"] == "ubuntu-latest"
    assert resolver["strategy"]["matrix"]["python-version"] == ["3.9", "3.10"]


def test_jsonl_archive_portability_uses_locked_uv():
    """No pip-only drift in the Windows/macOS/Linux x Python archive matrix."""
    workflow = (Path(__file__).parents[1] / ".github/workflows/jsonl-archive.yml").read_text(
        encoding="utf-8"
    )
    assert "os: [ubuntu-latest, windows-latest, macos-latest]" in workflow
    assert "python: ['3.12.15', '3.13.17', '3.14.9']" in workflow
    assert "node-version: '24.22.1'" in workflow
    assert "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2" in workflow
    assert "python-version: ${{ matrix.python }}" in workflow
    assert "run: uv run --locked --extra test pytest -q tests/test_jsonl_log_archive.py" in workflow
    assert 'version: "latest"' in workflow
    assert "actions/setup-python@" not in workflow
    steps = yaml.safe_load(workflow)["jobs"]["archive"]["steps"]
    setup_uv = [step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@")]
    assert len(setup_uv) == 1
    assert setup_uv[0]["with"]["python-version"] == "${{ matrix.python }}"
    assert "cache-python" not in setup_uv[0]["with"]
    # setup-uv owns installation; a second install breaks cached Windows links.
    assert all("uv python install" not in step.get("run", "") for step in steps)
    verify_python = [step for step in steps if step.get("name") == "Verify exact Python version"]
    assert len(verify_python) == 1
    assert verify_python[0]["run"] == (
        'uv run --no-project python -c "import sys; '
        "assert '.'.join(map(str, sys.version_info[:3])) == '${{ matrix.python }}', "
        'sys.version; print(sys.version)"'
    )
    assert "run: uv run --no-project python -S src/mcp_toolcall_lab/jsonl_log_archive.py --help" in workflow
    assert "pip install pytest" not in workflow
    assert "python -m pytest" not in workflow
