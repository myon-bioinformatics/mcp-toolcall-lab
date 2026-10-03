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
    assert 'pip install -e ".[test]"' in producer
    assert "run: pytest -q --junitxml=reports/pytest.xml" in producer
    assert "continue-on-error" not in producer
    assert "|| true" not in producer
    assert "if: always()" in producer
    assert "name: junit-python-3.11" in producer
    assert "path: reports/pytest.xml" in producer
    assert "if-no-files-found: error" in producer
    assert "needs: [changes, test]" in collector
    assert "if: always() && needs.changes.outputs.non_docs_changed == 'true'" in collector
    assert "reusable-junit-identity.yml@4dfda95d6573250477f991a0421fa6acb9bc0258" in collector
    assert 'expected-reports: \'["junit-python-3.11/pytest.xml"]\'' in collector
    assert "artifact-pattern: junit-python-3.11" in collector
    assert "actions: read" in collector
    assert "pages:" not in collector
