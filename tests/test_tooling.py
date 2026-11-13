"""Repository contracts: diagnostics, vendor lock wiring, parent tools and pins, JUnit production, Docker log projection."""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab import mcp_toolcall_lab_repository_diagnostics as diagnostics
from mcp_toolcall_lab.docker_logs import (
    digests_from_inspect, parse_compose_line, parse_compose_text, parse_docker_images_digests, redact, timeline_rows,
)
from mcp_toolcall_lab.github_public_resolver import candidate_urls, probe_url


# ═══ · mcp toolcall lab repository diagnostics ═══════════════════════════════════════════════════════════

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


def test_pages_renderer_is_the_shared_self_contained_page():
    html = diagnostics._renderer_page()
    assert "<title>myon-bioinformatics/mcp-toolcall-lab repository diagnostics</title>" in html
    assert "cdn.jsdelivr.net" not in html and '<link rel="stylesheet"' not in html
    assert "/* css/repository-diagnostics.css */" in html and "global.RepositoryDiagnostics = api" in html
    assert 'RepositoryDiagnostics.mount(document, "./repository-diagnostics.json");' in html
    assert "repository-diagnostics.jsonl" in html
    assert 'data-ui-theme="github-like"' in html


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


# ═══ · mcp toolcall lab retired vendor refs ══════════════════════════════════════════════════════════════
# Retired upstream source paths must not remain in the vendor lock.

def test_ironmate_sources_are_not_locked_anymore():
    root = Path(__file__).resolve().parents[1]
    entries = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))["files"]
    ironmate = [e for e in entries if e["repository"] == "myon-bioinformatics/Ironmate"]
    assert ironmate == []


# ═══ · mcp toolcall lab vendor automation ════════════════════════════════════════════════════════════════
# Regression checks for vendor.lock.json CI wiring.

WORKFLOW = ROOT / ".github/workflows/ci.yml"
VENDOR_SYNC_PIN = "26c442bc51b8a2837fe2556782b2937f396932af"


def _workflow():
    import yaml
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_vendor_lock_lists_expected_sources():
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    expected = {
        ("myon-bioinformatics/ascii_artist", "ascii_artist.py", "vendor/ascii_artist.py"),
        ("myon-bioinformatics/myon-bioinformatics", "gh_ops.py", "vendor/gh_ops.py"),
        ("myon-bioinformatics/myon-bioinformatics", "vendor_fleet_plan.py", "vendor/vendor_fleet_plan.py"),
        ("myon-bioinformatics/markdown", "markdown.py", "vendor/markdown.py"),
        ("myon-bioinformatics/ascii_artist", "LICENSE", "vendor/ascii_artist-LICENSE"),
        ("myon-bioinformatics/markdown", "LICENSE", "vendor/markdown-LICENSE"),
        ("myon-bioinformatics/myon-bioinformatics", "LICENSE", "vendor/myon-bioinformatics-LICENSE"),
        ("myon-bioinformatics/gh_identity", "gh_identity.py", "vendor/gh_identity.py"),
        ("myon-bioinformatics/gh_identity", "LICENSE", "vendor/gh_identity-LICENSE"),
        (
            "myon-bioinformatics/myon-bioinformatics",
            "njs_launch.py",
            "vendor/njs_launch.py",
        ),
        ("myon-bioinformatics/PySaiyanWeBoo", "pysaiyanweboo.py", "vendor/pysaiyanweboo.py"),
        ("myon-bioinformatics/PySaiyanWeBoo", "LICENSE", "vendor/pysaiyanweboo-LICENSE"),
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


# ═══ · mcp toolcall lab vendor staging ═══════════════════════════════════════════════════════════════════
# Vendor evidence staging was removed when vendor.lock.json became the sole record.

def test_workflows_no_longer_invoke_vendor_stage():
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        assert "Stage canonical vendor evidence" not in text
        assert "vendor_stage.py" not in text
        assert "vendor-promotion.json" not in text


# ═══ · parent tools ══════════════════════════════════════════════════════════════════════════════════════
# Offline compatibility of canonical parent tools with adjacent vendored GHI.

__all__ = []

import hashlib, json, subprocess, sys
VENDOR = ROOT / "vendor"


def test_parent_tools_match_recorded_bytes():
    entries = json.loads((ROOT / "vendor.lock.json").read_text())["files"]
    for entry in entries:
        if entry["repository"] == "myon-bioinformatics/myon-bioinformatics":
            data = (ROOT / entry["destination"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == entry["blob_sha"]


def test_gh_ops_help_is_standalone():
    result = subprocess.run([sys.executable, "-S", str(VENDOR / "gh_ops.py"), "--help"],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "git-push" in result.stdout


def test_git_push_dry_run_uses_actual_adjacent_identity_without_network(tmp_path):
    # A deliberately unusable remote and guarded real Git runner keep this offline.
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True,
                              text=True, check=True, timeout=15)
    git("init", "--initial-branch=refresh")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "fixture")
    git("remote", "add", "origin", "https://invalid.invalid/never/pushed.git")
    head = git("rev-parse", "HEAD").stdout.strip()
    code = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import gh_ops
assert pathlib.Path(gh_ops.gh_identity.__file__).resolve() == pathlib.Path(sys.argv[1], 'gh_identity.py').resolve()
actual_run = gh_ops.gh_identity._git_run
calls = []
def local_only(cwd, args, **kwargs):
    assert args[0] in {'branch', 'check-ref-format', 'rev-parse', 'remote'}
    assert kwargs.get('inherit_auth') is False
    calls.append(args[0])
    return actual_run(cwd, args, **kwargs)
gh_ops.gh_identity._git_run = local_only
receipt = gh_ops.git_push(cwd=sys.argv[2], write=False, timeout=10)
assert calls == ['branch', 'check-ref-format', 'rev-parse', 'remote']
print(json.dumps(receipt))
"""
    result = subprocess.run([sys.executable, "-S", "-c", code, str(VENDOR), str(tmp_path)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["ok"] and receipt["dry_run"]
    assert receipt["pushed"] is False
    assert receipt["head_sha"] == head
    assert receipt["branch"] == "refresh"
    assert receipt["mutation_status"] == receipt["verification_status"] == "not_attempted"
    assert "invalid.invalid" not in result.stdout
    assert not git("for-each-ref", "refs/remotes/").stdout


# ═══ · parent workflow pins ══════════════════════════════════════════════════════════════════════════════
# Local rollout contract; adopted vendor locks are deliberately independent.

PARENT = "myon-bioinformatics/myon-bioinformatics"
PARENT_WORKFLOW_PIN = "9e3da7ae1846e9604ebc24bb5178f7de094a4e77"


def test_parent_workflow_references_use_reviewed_rollout_commit():
    pins = []
    for path in sorted((ROOT / ".github/workflows").glob("*")):
        if path.suffix not in (".yml", ".yaml"):
            continue
        text = path.read_text(encoding="utf-8")
        # Capture mutable and shortened references too, rather than ignoring them.
        pins.extend(re.findall(r"uses:\s*['\"]?" + re.escape(PARENT) + r"/[^\s@'\"]+@([^\s'\"]+)", text))
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if re.fullmatch(r"\s+repository:\s*['\"]?" + re.escape(PARENT) + r"['\"]?\s*", line):
                refs = [re.fullmatch(r"\s+ref:\s*['\"]?([^\s'\"]+)['\"]?\s*", item) for item in lines[i + 1:i + 12]]
                if not any(re.fullmatch(r"\s+path:\s*['\"]?\.vendor-sync-tools['\"]?\s*", item) for item in lines[i + 1:i + 12]):
                    pins.append(next((m.group(1) for m in refs if m), None))
    assert pins, "shared parent references must remain observable"
    assert set(pins) == {PARENT_WORKFLOW_PIN}, pins


# ═══ · junit ci ══════════════════════════════════════════════════════════════════════════════════════════
# Exercise real pytest JUnit production without changing child outcomes.
#
# The shared pinned collector is exercised by Actions; this regression uses no
# runtime dependencies and never imports a repository-local JUnit collector.

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
    workflow = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    producer = workflow.split("  test:\n", 1)[1].split("  failure-identity:\n", 1)[0]
    collector = workflow.split("  failure-identity:\n", 1)[1].split("  public-resolver-compat:", 1)[0]
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
    assert 'expected-reports: \'["junit-python-3.12.15-node-26.12.1/pytest.xml", "junit-python-3.15-node-26.12.1/pytest.xml"]\'' in collector
    assert "artifact-pattern: junit-python-*-node-*" in collector
    assert "actions: read" in collector
    assert "pages:" not in collector


def test_public_resolver_compat_keeps_legacy_python_coverage():
    workflow = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
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
    assert "node-version: '26.12.1'" in workflow
    assert "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2" in workflow
    assert "python-version: ${{ matrix.python }}" in workflow
    assert "run: uv run --locked --only-group ci-stdlib --exact pytest -q tests/test_jsonl_log_archive.py" in workflow
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


# ═══ · junit failure matrix ══════════════════════════════════════════════════════════════════════════════
# Intentionally failing child tests that must yield inspectable JUnit XML.
#
# The parent pytest process remains green only if every negative fixture fails
# as expected and XML preserves its classification and traceback. No real
# production failure is suppressed or converted to success.

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


# ═══ · docker logs ═══════════════════════════════════════════════════════════════════════════════════════
# Compose log projection — timestamp / level / message, no new protocol.

SAMPLE = ROOT / "fixtures" / "docker_logs" / "compose.sample.log"


def test_sample_compose_log_has_time_level_and_service() -> None:
    rows = parse_compose_text(SAMPLE.read_text(encoding="utf-8"))
    assert rows
    first = rows[0]
    assert first["at"].startswith("2026-09-19T12:55:10")
    assert first["level"] == "INFO"
    assert first["service"] == "open-webui"
    assert "Application startup complete" in first["message"]
    assert first["source"] == "docker-compose"

    mcp = [row for row in rows if row["service"] == "mcp-mock"]
    assert any("event=initialize" in row["message"] for row in mcp)
    assert any(row.get("level") == "INFO" for row in mcp)

    lc = [row for row in rows if row["service"] == "librechat"]
    assert lc and lc[0]["level"] == "INFO"
    assert "mcp-mock:8000/mcp" in lc[0]["message"]


def test_title_task_and_mcp_initialize_are_visible_as_product_text() -> None:
    rows = parse_compose_text(SAMPLE.read_text(encoding="utf-8"))
    messages = " ".join(row["message"] for row in rows)
    assert "### Task:" in messages
    assert "initialize" in messages
    assert "POST /mcp" in messages


def test_secrets_are_redacted_in_the_projection() -> None:
    hidden = redact("Authorization: Bearer sk-mcp-toolcall-lab-example")
    assert "sk-mcp" not in hidden
    assert "[REDACTED]" in hidden
    line = parse_compose_line(
        "open-webui-1  | 2026-09-19T12:55:12.000000000Z ERROR failed with Authorization: Bearer sk-mcp-toolcall-lab-example"
    )
    assert line is not None
    assert "sk-mcp" not in line["message"]
    assert line["level"] == "ERROR"


def test_timeline_orders_mcp_jsonl_with_compose_lines(tmp_path: Path) -> None:
    compose = tmp_path / "compose.jsonl"
    mcp = tmp_path / "mcp-toolcalls.jsonl"
    compose.write_text(
        '{"at":"2026-09-19T12:55:10.140Z","message":"startup","service":"open-webui"}\n',
        encoding="utf-8",
    )
    mcp.write_text(
        '{"at":"2026-09-19T12:55:10.154Z","event":"initialize"}\n'
        '{"at":"2026-09-19T12:55:10.174Z","event":"tools/list"}\n',
        encoding="utf-8",
    )
    rows = timeline_rows([compose, mcp])
    assert [row.get("event") or row.get("message") for row in rows] == [
        "startup",
        "initialize",
        "tools/list",
    ]
    assert rows[1]["stream"] == "mcp"


def test_floating_tag_digest_is_collected_from_inspect_and_images_listing() -> None:
    inspect = {
        "RepoDigests": [],
        "Config": {"Image": "ghcr.io/open-webui/open-webui:main"},
        "Image": "sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    }
    assert digests_from_inspect(inspect) == []
    inspect["RepoDigests"] = ["ghcr.io/open-webui/open-webui@sha256:" + "a" * 64]
    assert digests_from_inspect(inspect) == [
        "ghcr.io/open-webui/open-webui@sha256:" + "a" * 64
    ]
    listing = (
        "ghcr.io/open-webui/open-webui:main\tsha256:" + "b" * 64
        + "\tsha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
        "unrelated/image:latest\tsha256:" + "c" * 64 + "\tsha256:ffff\n"
        "ghcr.io/open-webui/open-webui:main\t<none>\tsha256:deadbeef\n"
    )
    found = parse_docker_images_digests(
        listing,
        image_name="ghcr.io/open-webui/open-webui:main",
        image_id="sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )
    assert found == ["ghcr.io/open-webui/open-webui@sha256:" + "b" * 64]
