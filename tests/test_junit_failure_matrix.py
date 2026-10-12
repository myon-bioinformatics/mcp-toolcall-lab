"""Intentionally failing child tests that must yield inspectable JUnit XML.

The parent pytest process remains green only if every negative fixture fails
as expected and XML preserves its classification and traceback. No real
production failure is suppressed or converted to success.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


CHILD = """
import pytest
import unittest

def test_assertion_failure():
    assert 1 == 2, "intentional-assertion-sentinel"

@pytest.fixture
def broken_setup():
    raise RuntimeError("intentional-setup-sentinel")

def test_setup_error(broken_setup):
    pass

def test_runtime_error():
    raise ValueError("intentional-runtime-sentinel")

@pytest.mark.xfail(reason="intentional-expected-failure", strict=True)
def test_expected_failure():
    assert False, "intentional-xfail-sentinel"

@pytest.mark.skip(reason="intentional-skip-sentinel")
def test_skipped():
    assert False

class WrappedUnittest(unittest.TestCase):
    def test_wrapped_assertion(self):
        self.assertEqual(1, 2, "intentional-unittest-sentinel")

    def test_wrapped_error(self):
        raise LookupError("intentional-unittest-error-sentinel")
"""


def _run_negative_fixture(tmp_path: Path) -> tuple[subprocess.CompletedProcess[bytes], ET.Element]:
    source = tmp_path / "test_negative_fixture.py"
    source.write_text(CHILD, encoding="utf-8")
    xml = tmp_path / "reports" / "negative.xml"
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    command = [
        sys.executable, "-m", "pytest", "-q", "-c", os.devnull,
        "--rootdir", str(tmp_path), str(source), "--junitxml", str(xml),
    ]
    run = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=45)
    assert xml.is_file(), run.stderr.decode("utf-8", errors="replace")
    return run, ET.parse(xml).getroot()


def test_intentional_failures_are_preserved_in_junit(tmp_path):
    run, root = _run_negative_fixture(tmp_path)
    assert run.returncode == 1, "The negative test suite must actually fail"
    cases = {node.attrib["name"]: node for node in root.findall(".//testcase")}
    assert len(cases) == 7, sorted(cases)
    expected = {
        "test_assertion_failure": ("failure", "intentional-assertion-sentinel"),
        "test_setup_error": ("error", "intentional-setup-sentinel"),
        "test_runtime_error": ("failure", "intentional-runtime-sentinel"),
        "test_expected_failure": ("skipped", "intentional-expected-failure"),
        "test_skipped": ("skipped", "intentional-skip-sentinel"),
        "test_wrapped_assertion": ("failure", "intentional-unittest-sentinel"),
        "test_wrapped_error": ("failure", "intentional-unittest-error-sentinel"),
    }
    for name, (kind, sentinel) in expected.items():
        element = cases[name].find(kind)
        assert element is not None, (name, kind)
        assert sentinel in ET.tostring(element, encoding="unicode"), name
    assert "test_wrapped_assertion" in cases
    assert "test_wrapped_error" in cases


def test_intentional_failure_junit_summary_counts(tmp_path):
    run, root = _run_negative_fixture(tmp_path)
    assert run.returncode != 0
    suite = root.find(".//testsuite")
    assert suite is not None
    # pytest emits ordinary runtime exceptions as test failures, while
    # fixture setup errors are classified as errors.
    assert int(suite.attrib["tests"]) == 7
    assert int(suite.attrib["failures"]) == 4
    assert int(suite.attrib["errors"]) == 1
    assert int(suite.attrib["skipped"]) == 2


def test_invalid_junit_is_rejected_not_silently_counted(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text("<testsuite><testcase></testsuite>", encoding="utf-8")
    with pytest.raises(ET.ParseError):
        ET.parse(path)
