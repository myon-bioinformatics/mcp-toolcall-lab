"""Regression checks for vendor.lock.json CI wiring."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/test.yml"
VENDOR_SYNC_PIN = "1eda992aa6fa31a902e2c06428e53ab17e8fe3c1"


def _workflow():
    import yaml
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_vendor_lock_lists_expected_sources():
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    expected = {
        ("myon-bioinformatics/ascii_artist", "ascii_artist.py", "vendor/ascii_artist.py"),
        ("myon-bioinformatics/myon-bioinformatics", "gh_ops.py", "vendor/gh_ops.py"),
        ("myon-bioinformatics/markdown", "markdown.py", "vendor/markdown.py"),
        ("myon-bioinformatics/ascii_artist", "LICENSE", "vendor/ascii_artist-LICENSE"),
        ("myon-bioinformatics/markdown", "LICENSE", "vendor/markdown-LICENSE"),
        ("myon-bioinformatics/myon-bioinformatics", "LICENSE", "vendor/myon-bioinformatics-LICENSE"),
        ("myon-bioinformatics/gh_identity", "gh_identity.py", "vendor/gh_identity.py"),
        ("myon-bioinformatics/gh_identity", "LICENSE", "vendor/gh_identity-LICENSE"),
    }
    actual = {(e["repository"], e["source"], e["destination"]) for e in lock["files"]}
    assert actual == expected


def test_ci_verifies_vendor_lock_without_auto_update_jobs():
    ci = _workflow()
    assert {"push", "pull_request"} <= set(ci["on"])
    assert "workflow_dispatch" not in ci["on"]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "vendor_sync.py promote" not in text
    assert "vendor_sync.py update" not in text
    assert "sync_vendor_provenance" not in text
    assert "resolve-vendor" not in text
    assert "test-locked" not in text

    vendor_job = ci["jobs"]["vendor-locked"]
    test_job = ci["jobs"]["test"]
    assert vendor_job["permissions"] == test_job["permissions"] == {"contents": "read"}
    assert "continue-on-error" not in vendor_job
    assert test_job["needs"] == ["changes", "vendor-locked"]

    pins = [
        step["with"]["ref"]
        for job in (vendor_job, test_job)
        for step in job["steps"]
        if step.get("with", {}).get("repository") == "myon-bioinformatics/myon-bioinformatics"
    ]
    assert pins == [VENDOR_SYNC_PIN, VENDOR_SYNC_PIN]

    verify_steps = [
        step["run"]
        for step in vendor_job["steps"]
        if "vendor_sync.py check" in step.get("run", "")
    ]
    assert verify_steps
    assert any(
        step.get("run") == "uv run --locked --extra test pytest -q --junitxml=reports/pytest.xml"
        for step in test_job["steps"]
    )


@pytest.mark.parametrize("path", ["scripts/sync_vendor_provenance.py"])
def test_removed_local_vendor_provenance_helpers(path):
    assert not (ROOT / path).is_file()
