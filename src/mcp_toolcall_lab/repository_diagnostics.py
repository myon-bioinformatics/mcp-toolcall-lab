"""Static repository diagnostics for GitHub Pages.

This module is the mcp-toolcall-lab consumer of Ironmate's public repository
metadata contract v1 and web-ui's RepositoryDiagnostics renderer. It reuses
this repository's anonymous GitHub resolver for observations rather than
duplicating resolver semantics.
"""
from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from types import ModuleType
from typing import Any, Mapping

from .github_public_resolver import resolve_public_github

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "vendor" / "repository_metadata_contract.py"
CONTRACT_PROVENANCE_PATH = REPO_ROOT / "vendor" / "repository_metadata_contract.provenance.json"
REPOSITORY = "myon-bioinformatics/mcp-toolcall-lab"
WEB_UI_SHA = "adb23d7ba6ea94672b76457573f6655a081ee054"
WEB_UI_BASE = f"https://cdn.jsdelivr.net/gh/myon-bioinformatics/web-ui@{WEB_UI_SHA}"
JSON_NAME = "repository-diagnostics.json"
JSONL_NAME = "repository-diagnostics.jsonl"
PAGE_NAME = "repository-diagnostics.html"


def _load_contract() -> ModuleType:
    spec = importlib.util.spec_from_file_location("repository_metadata_contract_v1", CONTRACT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load repository metadata contract: {CONTRACT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git command failed: {' '.join(args)}")
    return result.stdout.strip()


def _tracked_bytes(root: Path = REPO_ROOT) -> int:
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    total = 0
    for item in raw.split(b"\0"):
        if not item:
            continue
        path = root / item.decode("utf-8", errors="surrogateescape")
        if path.is_file():
            total += path.stat().st_size
    return total


def build_record(
    env: Mapping[str, str] | None = None,
    *,
    now: str | None = None,
    working_tree_bytes: int | None = None,
) -> dict[str, Any]:
    """Build one canonical metadata v1 record from the current checkout."""
    env = os.environ if env is None else env
    contract = _load_contract()
    sha = (env.get("GITHUB_SHA") or "").strip() or _git(["rev-parse", "HEAD"])
    branch = (
        (env.get("GITHUB_HEAD_REF") or "").strip()
        or (env.get("GITHUB_REF_NAME") or "").strip()
        or _git(["branch", "--show-current"])
        or "detached"
    )
    timestamp = _git(["show", "-s", "--format=%cI", "HEAD"])
    subject = _git(["show", "-s", "--format=%s", "HEAD"])
    generated_at = now or datetime.now(UTC).isoformat()
    return contract.build_repository_record(
        full_name=REPOSITORY,
        sha=sha,
        branch=branch,
        timestamp=timestamp,
        subject=subject,
        generated_at=generated_at,
        working_tree_bytes=_tracked_bytes() if working_tree_bytes is None else working_tree_bytes,
        tooling={"python": platform.python_version()},
    )


def build_payload(
    record: dict[str, Any],
    *,
    probe: bool = True,
    opener: Any = None,
) -> dict[str, Any]:
    """Combine canonical metadata with anonymous resolver evidence."""
    kwargs: dict[str, Any] = {
        "ref": record["head"]["branch"],
        "path": "README.md",
        "probe": probe,
    }
    if opener is not None:
        kwargs["opener"] = opener
    try:
        resolver = resolve_public_github(record["repository"]["full_name"], **kwargs)
    except ValueError as exc:
        if "ref must be a simple Git ref" not in str(exc):
            raise
        resolver = {
            "repository": record["repository"]["full_name"],
            "ref": record["head"]["branch"],
            "path": "README.md",
            "auth": "anonymous",
            "candidates": [],
            "observations": [],
            "status": "not_checked",
            "reason": "unsupported_ref",
        }
    return {
        "schema_version": "1.0",
        "metadata": record,
        "resolver": resolver,
        "generated_at": record["generated_at"],
    }


def _renderer_page() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mcp-toolcall-lab repository diagnostics</title>
<link rel="stylesheet" href="{WEB_UI_BASE}/css/tokens.css">
<link rel="stylesheet" href="{WEB_UI_BASE}/css/base.css">
<link rel="stylesheet" href="{WEB_UI_BASE}/css/components.css">
<link rel="stylesheet" href="{WEB_UI_BASE}/css/themes/modern.css">
<link rel="stylesheet" href="{WEB_UI_BASE}/css/repository-diagnostics.css">
</head>
<body data-ui-theme="modern">
<main class="ui-page">
<header>
<h1 class="ui-title">mcp-toolcall-lab repository diagnostics</h1>
<p class="ui-muted">Canonical public metadata plus anonymous GitHub URL observations.</p>
</header>
<div id="repository-diagnostics"></div>
<section class="ui-panel">
<h2>Public URL observations</h2>
<p id="resolver-summary" class="ui-muted">Loading…</p>
<div id="resolver-observations" class="ui-grid"></div>
</section>
<p><a href="./repository-diagnostics.json">JSON</a> · <a href="./repository-diagnostics.jsonl">JSONL</a> · <a href="./">Back to Pages</a></p>
</main>
<script src="{WEB_UI_BASE}/js/repository-diagnostics.js"></script>
<script>
(async function () {{
  "use strict";
  const root = document.getElementById("repository-diagnostics");
  const summary = document.getElementById("resolver-summary");
  const observations = document.getElementById("resolver-observations");
  try {{
    const response = await fetch("./repository-diagnostics.json", {{cache: "no-store"}});
    if (!response.ok) throw new Error("diagnostics fetch failed");
    const payload = await response.json();
    root.innerHTML = RepositoryDiagnostics.render(payload.metadata);
    const resolver = payload.resolver || {{}};
    summary.textContent = "Auth: " + (resolver.auth || "unknown") +
      " · Status: " + (resolver.status || "unknown");
    observations.replaceChildren();
    (resolver.observations || []).forEach(function (item) {{
      const card = document.createElement("article");
      card.className = "ui-card";
      const title = document.createElement("h3");
      title.textContent = item.kind || "resource";
      const status = document.createElement("p");
      status.className = "ui-tag";
      status.textContent = item.status || "unknown";
      const code = document.createElement("p");
      code.className = "ui-muted";
      code.textContent = item.http_status === null || item.http_status === undefined
        ? "HTTP: not observed" : "HTTP: " + item.http_status;
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = item.url;
      card.append(title, status, code, link);
      observations.append(card);
    }});
  }} catch (error) {{
    summary.textContent = "Diagnostics could not be loaded.";
    observations.replaceChildren();
  }}
}})();
</script>
</body>
</html>
"""


def write_pages(
    out_dir: Path,
    *,
    probe: bool = True,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Write JSON, JSONL, and the reusable diagnostics page into a Pages tree."""
    contract = _load_contract()
    out_dir.mkdir(parents=True, exist_ok=True)
    record = build_record(env)
    payload = build_payload(record, probe=probe)

    (out_dir / JSON_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / JSONL_NAME).write_text(
        contract.to_jsonl(record),
        encoding="utf-8",
    )
    (out_dir / PAGE_NAME).write_text(_renderer_page(), encoding="utf-8")
    return payload


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "_site"
    write_pages(target)
