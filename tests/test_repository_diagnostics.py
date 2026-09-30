import hashlib
import http.client
import json
import os
import platform
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from mcp_toolcall_lab import repository_diagnostics as diagnostics
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
    contract = diagnostics._load_contract()
    return contract.build_repository_record(
        full_name="myon-bioinformatics/mcp-toolcall-lab",
        sha="168a284ff33fff88217e7f4252d50c6723a94ef9",
        branch="main",
        timestamp="2026-09-27T20:00:00+09:00",
        subject="feat: diagnostics",
        generated_at="2026-09-27T21:00:00+09:00",
        working_tree_bytes=1234,
        tooling={"python": "3.11"},
    )


def _assert_vendored_provenance(path, provenance_path, source_path, blob_sha, sha256):
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert provenance["source_repository"] == "myon-bioinformatics/Ironmate"
    assert provenance["source_path"] == source_path
    assert provenance["source_commit"] == "0aee64da2f8d0119a3ef9b955e5c3818f28aaf92"
    assert provenance["blob_sha"] == blob_sha
    assert provenance["sha256"] == sha256
    assert provenance["schema_version"] == "1.0"
    data = path.read_bytes()
    git_blob = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + bytes([0]) + data).hexdigest()
    assert git_blob == provenance["blob_sha"]
    assert hashlib.sha256(data).hexdigest() == provenance["sha256"]



def test_git_inspector_provenance_matches_canonical_baseline():
    provenance = json.loads(
        diagnostics.GIT_INSPECTOR_PROVENANCE_PATH.read_text(encoding="utf-8")
    )
    data = diagnostics.GIT_INSPECTOR_PATH.read_bytes()
    blob = hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + bytes([0]) + data
    ).hexdigest()
    assert provenance["source_repository"] == "myon-bioinformatics/myon-bioinformatics"
    assert provenance["source_path"] == "git_inspector.py"
    assert provenance["source_commit"] == "cffa7017c95634bfb6ed6b269d255d56680a894c"
    assert provenance["blob_sha"] == "abda0ba458c939240b9a9a6f0e4c8d640c76cf43"
    assert blob == provenance["blob_sha"]


def test_tracked_bytes_uses_canonical_inspector_paths(tmp_path, monkeypatch):
    (tmp_path / "space 日本語.txt").write_bytes(b"abc")
    fake = SimpleNamespace(
        ls_files=lambda root: {
            "paths": ["space 日本語.txt", "missing.txt"],
            "truncated": False,
        }
    )
    monkeypatch.setattr(diagnostics, "_load_git_inspector", lambda: fake)
    assert diagnostics._tracked_bytes(tmp_path) == 3


def test_tracked_bytes_rejects_truncated_inventory(tmp_path, monkeypatch):
    fake = SimpleNamespace(
        ls_files=lambda root: {"paths": ["partial"], "truncated": True}
    )
    monkeypatch.setattr(diagnostics, "_load_git_inspector", lambda: fake)
    with pytest.raises(RuntimeError, match="truncated"):
        diagnostics._tracked_bytes(tmp_path)


def test_vendored_contract_and_generator_provenance_match_baseline():
    _assert_vendored_provenance(
        diagnostics.CONTRACT_PATH,
        diagnostics.CONTRACT_PROVENANCE_PATH,
        "repository_metadata_contract.py",
        "a61a2949e58a42635b0830289e368b4125b1274b",
        "c8093d806756925b68978b5a40a218e4acd5daf43f2d7fc2e358cabf8dc39e9a",
    )
    _assert_vendored_provenance(
        diagnostics.GENERATOR_PATH,
        diagnostics.GENERATOR_PROVENANCE_PATH,
        "repository_metadata_generator.py",
        "eef572ce64e92bfecf0451235f884aa208044587",
        "a2edc91cc0a269d8b2fc6a9be1cfa0edbfae18604d53a1b9ebdcb72004be9a06",
    )


def test_build_record_ignores_mismatched_github_sha():
    expected = diagnostics._load_generator().git("rev-parse", "HEAD", cwd=diagnostics.REPO_ROOT)
    record = diagnostics.build_record(
        {"GITHUB_SHA": "0" * 40, "GITHUB_REF_NAME": "main"},
        working_tree_bytes=1234,
    )
    assert record["head"]["sha"] == expected
    assert record["head"]["short_sha"] == expected[:8]
    assert record["head"]["sha"] != "0" * 40


def test_generator_is_bound_to_pinned_vendored_contract():
    generator = diagnostics._load_generator()
    contract_file = Path(generator.build_repository_record.__globals__["__file__"]).resolve()
    assert contract_file == diagnostics.CONTRACT_PATH.resolve()


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


def test_build_record_matches_canonical_synthetic_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    _synthetic_checkout(checkout)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(diagnostics, "REPO_ROOT", checkout)

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

    contract = diagnostics._load_contract()
    expected = contract.build_repository_record(
        full_name=diagnostics.REPOSITORY,
        sha=_git(checkout, "rev-parse", "HEAD"),
        branch="feature/from-pr",
        timestamp=_git(checkout, "show", "-s", "--format=%cI", "HEAD"),
        subject=_git(checkout, "show", "-s", "--format=%s", "HEAD"),
        generated_at=now,
        working_tree_bytes=working_tree_bytes,
        tooling={"python": platform.python_version()},
    )

    assert record == expected
    assert record["head"]["branch"] == "feature/from-pr"
    assert record["measurements"]["working_tree_bytes"] == working_tree_bytes
    assert record["tooling"] == {"python": platform.python_version()}
    assert record["generated_at"] == now
    assert contract.to_json(record) == contract.to_json(expected)
    assert contract.to_jsonl(record) == contract.to_jsonl(expected)


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


def test_vendored_generator_executes_outside_repository_import_paths(tmp_path):
    checkout = tmp_path / "checkout"
    _synthetic_checkout(checkout)
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    generator_path = standalone / diagnostics.GENERATOR_PATH.name
    contract_path = standalone / diagnostics.CONTRACT_PATH.name
    shutil.copy2(diagnostics.GENERATOR_PATH, generator_path)
    shutil.copy2(diagnostics.CONTRACT_PATH, contract_path)

    cwd = tmp_path / "outside"
    cwd.mkdir()
    output_dir = tmp_path / "output"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["GITHUB_HEAD_REF"] = "feature/direct"
    env["GITHUB_REF_NAME"] = "87/merge"
    result = subprocess.run(
        [
            sys.executable,
            str(generator_path),
            "--repository",
            diagnostics.REPOSITORY,
            "--root",
            str(checkout),
            "--output-dir",
            str(output_dir),
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr

    json_record = json.loads(
        (output_dir / "repository-metadata.json").read_text(encoding="utf-8")
    )
    jsonl_record = json.loads(
        (output_dir / "repository-metadata.jsonl").read_text(encoding="utf-8")
    )
    assert json_record == jsonl_record
    assert json_record["head"]["sha"] == _git(checkout, "rev-parse", "HEAD")
    assert json_record["head"]["branch"] == "feature/direct"


def test_build_record_revalidates_after_generated_at_override(monkeypatch):
    invalid = sample_record()
    invalid["head"]["short_sha"] = "deadbeef"
    fake_generator = SimpleNamespace(
        record_from_checkout=lambda *args, **kwargs: invalid,
    )
    monkeypatch.setattr(diagnostics, "_load_generator", lambda: fake_generator)

    original_load_contract = diagnostics._load_contract
    canonical_flags = []

    def tracked_load_contract(*, canonical_name=False):
        canonical_flags.append(canonical_name)
        return original_load_contract(canonical_name=canonical_name)

    monkeypatch.setattr(diagnostics, "_load_contract", tracked_load_contract)
    with pytest.raises(ValueError, match="short_sha"):
        diagnostics.build_record(
            {},
            now="2026-09-29T12:34:56+00:00",
            working_tree_bytes=1234,
        )
    assert canonical_flags == [True]


def test_vendor_readme_refresh_updates_repository_metadata_provenance():
    readme = (diagnostics.REPO_ROOT / "vendor" / "README.md").read_text(encoding="utf-8")
    section = readme.split("# Vendored repository metadata producer", 1)[1]
    assert "BLOB_SHA=$(gh api" in section
    assert "--jq .sha" in section
    assert "SHA256=$(python3 -c" in section
    assert "source_commit" in section
    assert "blob_sha" in section
    assert "sha256" in section
    assert ".provenance.json" in section


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
    original_load_contract = diagnostics._load_contract
    canonical_flags = []

    def tracked_load_contract(*, canonical_name=False):
        canonical_flags.append(canonical_name)
        return original_load_contract(canonical_name=canonical_name)

    monkeypatch.setattr(diagnostics, "_load_contract", tracked_load_contract)
    payload = diagnostics.write_pages(tmp_path, probe=False)
    assert canonical_flags == [True]
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
