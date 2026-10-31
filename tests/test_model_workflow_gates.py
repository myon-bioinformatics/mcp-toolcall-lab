"""Offline CI policy checks: manual model validation is not a mocked CI pass."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
MANUAL = "github.event_name == 'workflow_dispatch'"
MAIN_MANUAL = MANUAL + " && github.ref == 'refs/heads/main'"
PORTABLE = {
    "name": "portable-build",
    "dockerfile": "docker/portable-pages/Dockerfile",
    "target": "build",
    "trust_args": "",
}


def _workflows():
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".github/workflows").glob("*.yml")
    }


def _events(workflow):
    # PyYAML's YAML 1.1 loader treats GitHub's unquoted `on` key as True.
    return workflow.get("on", workflow.get(True, {}))


def _assert_no_automatic_models(workflows):
    for filename, job_id in (
        ("portable-pages.yml", "build"),
        ("compaction-gguf-benchmark.yml", "live-gguf"),
    ):
        workflow = workflows[filename]
        assert set(_events(workflow)) == {"workflow_dispatch"}
        assert workflow["jobs"][job_id]["if"] == MANUAL
    pages = workflows["portable-pages.yml"]["jobs"]
    assert pages["deploy"]["if"] == MAIN_MANUAL
    assert pages["deploy"]["needs"] == "build"
    upload = next(step for step in pages["build"]["steps"]
                  if step.get("uses", "").startswith("actions/upload-pages-artifact@"))
    assert upload["if"] == MAIN_MANUAL

    runtime = workflows["component-runtime.yml"]
    assert set(_events(runtime)) == {"pull_request", "workflow_dispatch"}
    automatic = runtime["jobs"]["runtime"]["strategy"]["matrix"]["include"]
    assert {row["name"] for row in automatic} == {
        "librechat-mcp", "openwebui-mcp", "openai-mock", "stub-cpu", "stub-front",
    }
    assert all(row["dockerfile"] != PORTABLE["dockerfile"] for row in automatic)
    live = runtime["jobs"]["portable-live"]
    assert live["if"] == MANUAL  # Job-level skip, not successful skipped steps.
    assert live["strategy"]["matrix"]["include"] == [PORTABLE]
    assert not live.get("continue-on-error", False)
    budget = workflows["budget-quality-gguf.yml"]
    assert set(_events(budget)) == {"workflow_dispatch"}
    assert all(job["if"] == MANUAL for job in budget["jobs"].values())


def test_all_model_routes_are_manual_and_pages_has_no_automatic_replacement():
    workflows = _workflows()
    _assert_no_automatic_models(workflows)
    publishers = []
    for filename, workflow in workflows.items():
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("actions/deploy-pages@"):
                    publishers.append(filename)
    assert publishers == ["portable-pages.yml"]
    assert workflows["portable-pages.yml"]["jobs"]["deploy"]["environment"]["name"] == "github-pages"


@pytest.mark.parametrize("damage", [
    "pages_push", "pages_job", "pages_deploy", "pages_upload",
    "compaction_pr", "compaction_job", "runtime_matrix", "runtime_job", "budget_push",
])
def test_policy_rejects_each_reopened_automatic_model_or_publication_route(damage):
    workflows = deepcopy(_workflows())
    pages = workflows["portable-pages.yml"]
    compaction = workflows["compaction-gguf-benchmark.yml"]
    runtime = workflows["component-runtime.yml"]
    if damage == "pages_push":
        _events(pages)["push"] = {"branches": ["main"]}
    elif damage == "pages_job":
        pages["jobs"]["build"]["if"] = "always()"
    elif damage == "pages_deploy":
        pages["jobs"]["deploy"]["if"] = "github.ref == 'refs/heads/main'"
    elif damage == "pages_upload":
        next(step for step in pages["jobs"]["build"]["steps"]
             if step.get("uses", "").startswith("actions/upload-pages-artifact@"))["if"] = "always()"
    elif damage == "compaction_pr":
        _events(compaction)["pull_request"] = None
    elif damage == "compaction_job":
        compaction["jobs"]["live-gguf"]["if"] = "always()"
    elif damage == "runtime_matrix":
        runtime["jobs"]["runtime"]["strategy"]["matrix"]["include"].append(PORTABLE)
    elif damage == "runtime_job":
        runtime["jobs"]["portable-live"]["if"] = "always()"
    else:
        _events(workflows["budget-quality-gguf.yml"])["push"] = None
    with pytest.raises(AssertionError):
        _assert_no_automatic_models(workflows)


def test_manual_runtime_retains_real_build_trust_and_original_artifact_contract():
    jobs = _workflows()["component-runtime.yml"]["jobs"]
    automatic = {step["name"]: step for step in jobs["runtime"]["steps"] if "name" in step}
    manual = {step["name"]: step for step in jobs["portable-live"]["steps"] if "name" in step}
    assert jobs["portable-live"]["name"] == "runtime (portable-build, docker/portable-pages/Dockerfile, build)"
    for name in automatic:
        assert manual[name] == automatic[name]
    assert "docker build --pull --no-cache" in manual["Rebuild pinned runtime with no layer cache"]["run"]
    assert "--live" in manual["Verify actual container trust and installed component versions"]["run"]
    assert "node --version" in manual["Record portable Node version"]["run"]
    assert manual["Preserve runtime receipts even on failure"]["with"]["name"] == "runtime-${{ matrix.name }}"
    assert "browser-protocol" in jobs


def test_manual_pages_and_compaction_keep_actual_validation_not_mock_replacements():
    workflows = _workflows()
    pages = {step["name"]: step for step in workflows["portable-pages.yml"]["jobs"]["build"]["steps"] if "name" in step}
    export = pages["Export static files from Docker build"]["run"]
    assert "--target artifact" in export and "--target summary-handoff" in export
    assert "assert python['model_inference_performed'] is True" in pages["Verify exported runtime without the source checkout"]["run"]
    assert "--require-generated" in pages["Exercise browser input, snapshot miss and stale-result handling"]["run"]
    steps = {step["name"]: step for step in workflows["compaction-gguf-benchmark.yml"]["jobs"]["live-gguf"]["steps"] if "name" in step}
    for name in ("Download and verify pinned GGUF model", "Start digest-pinned llama.cpp server on loopback",
                 "Paired live GGUF evaluation", "Paired Aoi published propositions evaluation",
                 "Paired Aoi complete 114-proposition evaluation", "Validate report redaction and add numeric summary",
                 "Upload synthetic model evaluation and JUnit evidence"):
        assert name in steps
        assert not steps[name].get("continue-on-error", False)


def test_matrix_setup_precedes_bare_python_and_keeps_exact_pins_and_reports():
    workflows = _workflows()
    job = workflows["test.yml"]["jobs"]["test"]
    setup_index = next(i for i, step in enumerate(job["steps"])
                       if step.get("uses", "").startswith("astral-sh/setup-uv@"))
    setup = job["steps"][setup_index]["with"]
    assert setup["version"] == "0.13.5"
    assert setup["activate-environment"] is True
    assert setup["enable-cache"] is False
    for index, step in enumerate(job["steps"]):
        if step.get("run", "").lstrip().startswith("python "):
            assert setup_index < index
    assert job["strategy"]["matrix"]["include"] == [
        {"python-version": "3.12.15", "node-version": "24.22.1"},
        {"python-version": "3.12.15", "node-version": "26.12.1"},
        {"python-version": "3.15.1", "node-version": "26.12.1"},
    ]
    assert workflows["jsonl-archive.yml"]["jobs"]["archive"]["strategy"]["matrix"]["python"] == ["3.12.15", "3.13.17", "3.14.9"]
    reports = workflows["test.yml"]["jobs"]["junit-identity"]["with"]["expected-reports"]
    for row in job["strategy"]["matrix"]["include"]:
        assert f"junit-python-{row['python-version']}-node-{row['node-version']}/pytest.xml" in reports
