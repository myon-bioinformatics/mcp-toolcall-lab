"""Fleet contracts through the parent's shared checks (vendor/fleet_check.py): the vendor lock, parent pins,
vendor wiring in CI and the dependency profiles of the small CI lanes."""
from __future__ import annotations
import unittest

import json, re, tomllib
from importlib.metadata import entry_points

import pytest
import yaml

from vendor import fleet_check as fleet

ROOT = fleet.repo_root(__file__)
PARENT_WORKFLOW_PIN = "main"  # 🔁 own sources follow main (2026-11-28)
ACCEPTED = {"vendor/fleet_check.py"}  # 🔁 locked destinations; they follow main


# ═══ · vendor lock and parent pins (fleet_check) ════════════════════════════════════════════════════════════

def test_vendor_lock_matches_checked_in_bytes() -> None:
    # Every row, parent tools included: full-SHA ref == commit, blob_sha and sha256 of the placed bytes.
    assert set(json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))) == {"schema", "files"}
    rows = fleet.check_lock(ROOT)
    assert (rows["vendor/fleet_check.py"]["repository"], rows["vendor/fleet_check.py"]["source"]) == (fleet.PARENT, "fleet_check.py")


def test_parent_workflow_references_use_reviewed_rollout_commit():
    # Local rollout contract; adopted vendor locks are deliberately independent.
    pins = fleet.parent_refs(ROOT, skip=(".vendor-sync-tools",))
    assert pins, "shared parent references must remain observable"
    assert set(pins) == {PARENT_WORKFLOW_PIN}, pins


def test_pysaiyanweboo_is_one_pin_and_not_vendored():
    """📦 The pages group and the test extra pin the same PySaiyanWeBoo commit; no vendored copy drifts beside them."""
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = [d for d in project["project"]["optional-dependencies"]["test"] if d.startswith("pysaiyanweboo @")]
    assert pins == project["dependency-groups"]["pages"], pins
    assert not (ROOT / "vendor" / "pysaiyanweboo.py").exists()


# ═══ · retired vendor refs ════════════════════════════════════════════════════════════
# Retired upstream source paths must not remain in the vendor lock.

def test_ironmate_sources_are_not_locked_anymore():
    entries = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))["files"]
    ironmate = [e for e in entries if e["repository"] == "myon-bioinformatics/Ironmate"]
    assert ironmate == []


# ═══ · vendor automation ════════════════════════════════════════════════════════════
# Regression checks for vendor.lock.json CI wiring.

WORKFLOW = ROOT / ".github/workflows/ci.yml"
VENDOR_SYNC_PIN = "main"  # 🔁 own sources follow main (2026-11-28)


def _workflow():
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
        (fleet.PARENT, "fleet_check.py", "vendor/fleet_check.py"),
        ("myon-bioinformatics/cli_args", "cli_args.py", "vendor/cli_args.py"),
        ("myon-bioinformatics/cli_args", "LICENSE", "vendor/cli_args-LICENSE"),
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


# ═══ · vendor staging ════════════════════════════════════════════════════════════
# Vendor evidence staging was removed when vendor.lock.json became the sole record.

def test_workflows_no_longer_invoke_vendor_stage():
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        assert "Stage canonical vendor evidence" not in text
        assert "vendor_stage.py" not in text
        assert "vendor-promotion.json" not in text


# ═══ · stdlib-only lane profile ════════════════════════════════════════════════════════════
# Keep stdlib-only CI lanes small without dropping their existing plugins.

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
    for path in ("pyproject.toml", "uv.lock", "tests/test_fleet.py"):
        assert workflow.count(f"      - {path}\n") == 2


def test_chat_contract_workflow_keeps_python_and_deno_checks() -> None:
    workflow = (ROOT / ".github/workflows/chat-stub-contract.yml").read_text(encoding="utf-8")
    assert "uv run --locked --only-group ci-stdlib --exact python -m pytest -q tests/test_chat_contract.py" in workflow
    assert "PYTHONPATH: src" in workflow
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in workflow
    assert "deno check deploy/chat-stub/main.ts deploy/chat-stub/test_main.ts" in workflow
    assert "deno test --allow-read deploy/chat-stub/test_main.ts" in workflow
    for path in ("pyproject.toml", "uv.lock", "tests/test_fleet.py",
                 "tests/test_chat_contract.py", "fixtures/prompt_experiments/**",
                 "deploy/chat-stub/**", ".github/workflows/chat-stub-contract.yml"):
        assert workflow.count(f'      - "{path}"\n') == 1


# ═══ · screenshot lane profile ════════════════════════════════════════════════════════════
# The screenshot job keeps its real browser while omitting unrelated servers.

def test_screenshot_group_is_optional_and_reuses_locked_browser():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding="utf-8"))
    group = project['dependency-groups']['ci-wiki-screenshot']
    assert group == project['project']['optional-dependencies']['browser-test']
    assert group == ['playwright>=1.64.1']
    defaults = project['tool']['uv'].get('default-groups', ['dev'])
    assert isinstance(defaults, list)
    assert all(isinstance(group, str) for group in defaults)
    assert 'ci-wiki-screenshot' not in defaults
    assert project['project']['dependencies'] == [
        'fastmcp>=4.1.3', 'mcp>=2.3.0', 'pydantic>=2.14.1',
    ]
    assert 'test' in project['project']['optional-dependencies']
    lock = tomllib.loads((ROOT / 'uv.lock').read_text(encoding="utf-8"))
    package = next(p for p in lock['package'] if p['name'] == 'mcp-toolcall-lab')
    assert package['dev-dependencies']['ci-wiki-screenshot'] == [{'name': 'playwright'}]


def test_all_screenshot_commands_use_the_same_exact_group():
    workflow = (ROOT / '.github/workflows/wikipedia-article-screenshot.yml').read_text(encoding="utf-8")
    prefix = 'uv run --locked --only-group ci-wiki-screenshot --exact '
    commands = [line.strip().removeprefix('run: ') for line in workflow.splitlines()
                if line.strip().startswith('run: ')]
    assert commands == [
        prefix + 'playwright install --with-deps --only-shell chromium',
        prefix + 'python scripts/wikipedia_article_screenshot.py --out test-results/wiki-screenshots',
        prefix + 'python scripts/wikipedia_article_screenshot.py --live --out test-results/wiki-screenshots',
    ]
    assert "github.event_name != 'workflow_dispatch' || inputs.live_wikipedia != true" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.live_wikipedia == true" in workflow
    assert 'MCP_TOOLCALL_LAB_WIKIPEDIA_FIXTURE: ${{ github.workspace }}/fixtures/wikipedia/exampleville_extract.json' in workflow
    assert 'MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA: "1"' in workflow
    assert 'if: always()' in workflow
    assert 'if-no-files-found: error' in workflow
    for path in ['pyproject.toml', 'uv.lock', 'tests/test_fleet.py']:
        assert f'      - "{path}"' in workflow
    # The script supplies the same source tree without installing the server package.
    script = (ROOT / 'scripts/wikipedia_article_screenshot.py').read_text(encoding="utf-8")
    assert 'sys.path.insert(0, str(ROOT / "src"))' in script
    assert '"--full-page"' in script
    assert 'wiki-title-entered.png' in script and 'wiki-heading-selected.png' in script


# 🧰 Scripts: 📝 each one describes itself and ⌨️ one that reads sys.argv declares its arguments (argparse /
# cli_args). Read with ast, never run; the portfolio's script catalog reads the same facts.

class ScriptsDescribeThemselves(unittest.TestCase):
    def test_every_script_has_a_description_and_declared_arguments(self):
        fleet.check_scripts(fleet.repo_root(__file__))

    def test_pull_request_ci_stays_quick(self):
        # ⏱ push limited to branches, superseded PR runs cancelled, every job timed (fleet_check.check_ci)
        fleet.check_ci(fleet.repo_root(__file__))
