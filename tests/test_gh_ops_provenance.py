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


# Hard pins, like test_stub_pages.py does for markdown.py: rewriting only the
# JSON must not be enough to change what this repo claims to vendor.
PINNED_COMMIT = "dce15333d100d1163b1b706d8e8de769a1e116be"  # browser-test-kit#10 merge
PINNED_BLOB_SHA = "e4bd079300087a275c4fe577399e3230e5cd5b54"
PINNED_SHA256 = "482a8e5f6d267d476d5675e548815d2588fb2256572fc154107180385e837689"


def test_provenance_matches_hard_pins():
    recorded = load_provenance()
    assert recorded["commit"] == PINNED_COMMIT
    assert recorded["blob_sha"] == PINNED_BLOB_SHA
    assert recorded["sha256"] == PINNED_SHA256


def test_provenance_names_the_source_repo_and_merged_pr():
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
