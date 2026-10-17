import http.client
import json
import os
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from mcp_toolcall_lab import mcp_toolcall_lab_repository_diagnostics as diagnostics
from mcp_toolcall_lab.github_public_resolver import candidate_urls, probe_url


class Response(BytesIO):
    def __init__(self, status=200, url="https://example.test/final", headers=None):
        super().__init__(b"")
        self.status = status
        self._url = url
        self.headers = headers or {}

    def getcode(self):
        return self.status

    def geturl(self):
        return self._url


def sample_record():
    gh = diagnostics._load_gh_identity()
    return gh.validate_repository_metadata_v1(
        {
            "schema_version": gh.REPOSITORY_METADATA_V1_SCHEMA,
            "repository": {"full_name": "myon-bioinformatics/mcp-toolcall-lab"},
            "head": {
                "sha": "168a284ff33fff88217e7f4252d50c6723a94ef9",
                "short_sha": "168a284f",
                "branch": "main",
                "timestamp": "2026-09-27T20:00:00+09:00",
                "subject": "feat: diagnostics",
            },
            "measurements": {
                "github_reported_size_bytes": None,
                "working_tree_bytes": 1234,
                "release_artifact_bytes": None,
            },
            "tooling": {"python": "3.11"},
            "generated_at": "2026-09-27T21:00:00+09:00",
        }
    )


def test_tracked_bytes_uses_canonical_inspector_paths(tmp_path, monkeypatch):
    (tmp_path / "space 日本語.txt").write_bytes(b"abc")
    fake = SimpleNamespace(
        ls_files=lambda root: {
            "paths": ["space 日本語.txt", "missing.txt"],
            "truncated": False,
        }
    )
    monkeypatch.setattr(
        diagnostics,
        "_tracked_file_inventory",
        lambda root, **kwargs: fake.ls_files(root),
    )
    assert diagnostics._tracked_bytes(tmp_path) == 3


def test_tracked_bytes_rejects_truncated_inventory(tmp_path, monkeypatch):
    fake = SimpleNamespace(
        ls_files=lambda root: {"paths": ["partial"], "truncated": True}
    )
    monkeypatch.setattr(
        diagnostics,
        "_tracked_file_inventory",
        lambda root, **kwargs: fake.ls_files(root),
    )
    with pytest.raises(RuntimeError, match="truncated"):
        diagnostics._tracked_bytes(tmp_path)


def test_build_record_ignores_mismatched_github_sha():
    expected = _git(diagnostics.REPO_ROOT, "rev-parse", "HEAD")
    record = diagnostics.build_record(
        {"GITHUB_SHA": "0" * 40, "GITHUB_REF_NAME": "main"},
        working_tree_bytes=1234,
    )
    assert record["head"]["sha"] == expected
    assert record["head"]["short_sha"] == expected[:8]
    assert record["head"]["sha"] != "0" * 40


def _git(root, *args, env=None):
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return result.stdout.strip()


def _synthetic_checkout(root):
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Repository Metadata Test")
    _git(root, "config", "user.email", "metadata-test@example.invalid")
    (root / "README.md").write_text("synthetic checkout\n", encoding="utf-8")
    _git(root, "add", "README.md")
    commit_env = os.environ.copy()
    commit_env["GIT_AUTHOR_DATE"] = "2026-09-29T01:02:03+00:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-09-29T01:02:03+00:00"
    _git(root, "commit", "-m", "feat: synthetic 日本語", env=commit_env)
    _git(root, "branch", "-M", "work")


@pytest.mark.parametrize(
    "python_version,command_versions,expected_tooling",
    [
        pytest.param(
            "3.11.9",
            {"git": "2.45.0", "gh": "2.61.0", "node": "22.1.0", "npm": "10.8.0", "npx": "10.8.0"},
            {"python": "3.11.9", "git": "2.45.0", "gh": "2.61.0", "node": "22.1.0", "npm": "10.8.0", "npx": "10.8.0"},
            id="all-present",
        ),
        pytest.param(
            "3.11.9", {"git": "2.45.0"},
            {"python": "3.11.9", "git": "2.45.0"}, id="partial",
        ),
        pytest.param("not-a-version", {}, {}, id="empty"),
    ],
)
def test_build_record_matches_canonical_synthetic_checkout(
    tmp_path, monkeypatch, python_version, command_versions, expected_tooling,
):
    checkout = tmp_path / "checkout"
    _synthetic_checkout(checkout)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(diagnostics, "REPO_ROOT", checkout)

    gh = diagnostics._load_gh_identity()

    def fake_tool_versions(commands=(), *, include_python=False, timeout=5.0):
        observed = {}
        if include_python:
            match = gh._RM_TOOL_PATTERNS["python"].fullmatch(python_version)
            if match:
                observed["python"] = match.group("v")
        for command in commands:
            version = command_versions.get(command)
            if version is not None:
                observed[command] = version
        return observed

    monkeypatch.setattr(gh, "tool_versions", fake_tool_versions)
    monkeypatch.setattr(diagnostics, "_load_gh_identity", lambda: gh)
    env = {
        "GITHUB_HEAD_REF": "feature/from-pr",
        "GITHUB_REF_NAME": "87/merge",
        "GITHUB_SHA": "f" * 40,
    }
    now = "2026-09-29T12:34:56+00:00"
    working_tree_bytes = 4321
    record = diagnostics.build_record(
        env,
        now=now,
        working_tree_bytes=working_tree_bytes,
    )

    expected = gh.repository_metadata_v1(
        str(checkout),
        repository=diagnostics.REPOSITORY,
        env=env,
        measurements={"working_tree_bytes": working_tree_bytes},
        tooling=expected_tooling,
        generated_at=now,
    )

    assert record == expected
    assert record["head"]["branch"] == "feature/from-pr"
    assert record["measurements"]["working_tree_bytes"] == working_tree_bytes
    assert record["tooling"] == expected_tooling
    assert record["generated_at"] == now


def test_build_record_measures_tracked_bytes_from_repository_root(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    _synthetic_checkout(checkout)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(diagnostics, "REPO_ROOT", checkout)

    record = diagnostics.build_record(
        {},
        now="2026-09-29T12:34:56+00:00",
    )

    assert record["head"]["branch"] == "work"
    assert record["measurements"]["working_tree_bytes"] == len(
        (checkout / "README.md").read_bytes()
    )


def test_build_record_revalidates_after_generated_at_override(monkeypatch):
    gh = diagnostics._load_gh_identity()
    invalid = sample_record()
    invalid["head"]["short_sha"] = "deadbeef"

    def broken_metadata_v1(*args, **kwargs):
        return dict(invalid)

    validate_calls = []
    original_validate = gh.validate_repository_metadata_v1

    def tracked_validate(record):
        validate_calls.append(record)
        return original_validate(record)

    monkeypatch.setattr(gh, "repository_metadata_v1", broken_metadata_v1)
    monkeypatch.setattr(gh, "validate_repository_metadata_v1", tracked_validate)
    monkeypatch.setattr(diagnostics, "_load_gh_identity", lambda: gh)
    with pytest.raises(ValueError, match="short_sha"):
        diagnostics.build_record(
            {},
            now="2026-09-29T12:34:56+00:00",
            working_tree_bytes=1234,
        )
    assert len(validate_calls) == 1


def test_vendor_readme_documents_gh_identity_metadata():
    readme = (diagnostics.REPO_ROOT / "vendor" / "README.md").read_text(encoding="utf-8")
    section = readme.split("# Vendored `gh_identity.py`", 1)[1]
    assert "validate_repository_metadata_v1" in section
    assert "vendor.lock.json" in section
    assert "shim is not vendored" in section


def test_payload_reuses_public_resolver_without_probe():
    payload = diagnostics.build_payload(sample_record(), probe=False)
    assert payload["metadata"]["schema_version"] == "1.0"
    assert payload["resolver"]["auth"] == "anonymous"
    assert payload["resolver"]["status"] == "not_checked"
    assert payload["resolver"]["observations"] == []


def test_pages_renderer_is_pinned_and_links_json_and_jsonl():
    html = diagnostics._renderer_page()
    assert diagnostics.WEB_UI_SHA in html
    assert "repository-diagnostics.json" in html
    assert "repository-diagnostics.jsonl" in html
    assert "RepositoryDiagnostics.render(payload.metadata)" in html
    assert "textContent = item.url" in html


def test_write_pages_outputs_json_jsonl_and_html(tmp_path, monkeypatch):
    record = sample_record()
    monkeypatch.setattr(diagnostics, "build_record", lambda env=None: record)
    payload = diagnostics.write_pages(tmp_path, probe=False)
    assert payload["metadata"] == record
    parsed = json.loads((tmp_path / diagnostics.JSON_NAME).read_text(encoding="utf-8"))
    assert parsed["metadata"] == record
    jsonl = json.loads((tmp_path / diagnostics.JSONL_NAME).read_text(encoding="utf-8"))
    assert jsonl == record
    assert (tmp_path / diagnostics.PAGE_NAME).is_file()


@pytest.mark.parametrize("repo", ["a.b/demo", "./demo", "bad-/demo", "octo/..", "octo/."])
def test_resolver_rejects_invalid_repository_identity(repo):
    with pytest.raises(ValueError):
        candidate_urls(repo)


def test_resolver_handles_user_pages_repository():
    rows = candidate_urls("octo/octo.github.io")
    pages = next(row["url"] for row in rows if row["kind"] == "pages")
    assert pages == "https://octo.github.io/"


def test_lowercase_rate_limit_header_and_retry_after_are_rate_limited():
    def lower_opener(request, timeout):
        raise HTTPError(request.full_url, 403, "limited", {"x-ratelimit-remaining": "0"}, None)

    assert probe_url("https://api.github.com/x", opener=lower_opener)["status"] == "rate_limited"

    def retry_opener(request, timeout):
        raise HTTPError(request.full_url, 403, "limited", {"retry-after": "60"}, None)

    assert probe_url("https://api.github.com/x", opener=retry_opener)["status"] == "rate_limited"


@pytest.mark.parametrize(
    "exc",
    [
        http.client.BadStatusLine("garbled"),
        http.client.IncompleteRead(b"partial", 10),
    ],
)
def test_http_protocol_failures_are_unverified(exc):
    def opener(request, timeout):
        raise exc

    result = probe_url("https://example.test/x", opener=opener)
    assert result["status"] == "unverified"
    assert result["http_status"] is None
    assert result["evidence"] == "network_error"


@pytest.mark.parametrize("branch", ["feat+x", "renovate/@types-node", "fix/日本語"])
def test_unsupported_ref_degrades_to_not_checked(branch):
    record = sample_record()
    record["head"]["branch"] = branch
    payload = diagnostics.build_payload(record, probe=True)
    assert payload["resolver"]["status"] == "not_checked"
    assert payload["resolver"]["reason"] == "unsupported_ref"
    assert payload["resolver"]["observations"] == []
    assert payload["resolver"]["candidates"] == []

