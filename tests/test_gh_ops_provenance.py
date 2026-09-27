"""Vendored gh_ops.py must match the pinned commit/blob/sha256 (mirrors test_stub_pages.py's
markdown.py provenance check, see docs/github_ops_mcp.md for the refresh command)."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_toolcall_lab.markdown_lib import git_blob_sha

ROOT = Path(__file__).resolve().parents[1]
GH_OPS_PATH = ROOT / "vendor" / "gh_ops.py"
PROVENANCE_PATH = ROOT / "vendor" / "gh_ops.provenance.json"


def load_provenance() -> dict[str, str]:
    return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))


def test_vendored_gh_ops_matches_recorded_provenance():
    import hashlib

    recorded = load_provenance()
    digest = hashlib.sha256(GH_OPS_PATH.read_bytes()).hexdigest()
    blob = git_blob_sha(GH_OPS_PATH)
    assert blob == recorded["blob_sha"]
    assert digest == recorded["sha256"]


def test_provenance_names_the_source_pr_and_branch():
    recorded = load_provenance()
    assert recorded["repository"] == "https://github.com/myon-bioinformatics/browser-test-kit"
    assert recorded["path"] == "scripts/gh_ops.py"
    assert len(recorded["commit"]) == 40
    assert len(recorded["blob_sha"]) == 40
    assert len(recorded["sha256"]) == 64
    readme = (ROOT / "vendor" / "README.md").read_text(encoding="utf-8")
    assert "ref=${COMMIT}" in readme
    assert "browser-test-kit/pull/10" in readme


def test_vendored_gh_ops_is_stdlib_only():
    """No third-party imports -- it must run under ``python -S`` per its own docstring."""
    source = GH_OPS_PATH.read_text(encoding="utf-8")
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "GhOpsError" in source
    assert "def scrub(" in source
