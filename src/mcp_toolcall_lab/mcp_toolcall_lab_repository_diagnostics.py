"""Static repository diagnostics for GitHub Pages.

This module is the mcp-toolcall-lab consumer of gh_identity's repository
metadata v1 contract and web-ui's RepositoryDiagnostics renderer. It reuses
this repository's anonymous GitHub resolver for observations rather than
duplicating resolver semantics.
"""
from __future__ import annotations

import importlib.util, json, os, subprocess, sys
from pathlib import Path; from types import ModuleType; from typing import Any, Mapping

from .github_public_resolver import diagnostics_payload

REPO_ROOT = Path(__file__).resolve().parents[2]
GH_IDENTITY_PATH = REPO_ROOT / "vendor" / "gh_identity.py"
REPOSITORY = "myon-bioinformatics/mcp-toolcall-lab"
_TOOLING_COMMANDS = ("git", "gh", "node", "npm", "npx")
JSON_NAME = "repository-diagnostics.json"
JSONL_NAME = "repository-diagnostics.jsonl"
PAGE_NAME = "repository-diagnostics.html"


def _load_gh_identity() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mcp_toolcall_lab_vendor_gh_identity", GH_IDENTITY_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load gh_identity: {GH_IDENTITY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metadata_jsonl(record: dict[str, Any]) -> str:
    gh = _load_gh_identity()
    gh.validate_repository_metadata_v1(record)
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _tracked_file_inventory(root: Path, *, max_files: int = 10_000, max_bytes: int = 1_000_000) -> dict[str, Any]:
    """Bounded tracked-file inventory via ``git ls-files`` (no vendored helper)."""
    proc = subprocess.run(
        ["git", "-C", str(root), "--no-pager", "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("git ls-files failed")
    raw = proc.stdout[:max_bytes]
    byte_truncated = len(proc.stdout) > max_bytes
    if byte_truncated and raw and not raw.endswith(b"\0"):
        boundary = raw.rfind(b"\0")
        raw = b"" if boundary < 0 else raw[: boundary + 1]
    paths = [item.decode("utf-8", "replace") for item in raw.split(b"\0") if item]
    record_truncated = len(paths) > max_files
    return {"paths": paths[:max_files], "truncated": byte_truncated or record_truncated}


def _tracked_bytes(root: Path = REPO_ROOT) -> int:
    observation = _tracked_file_inventory(root)
    if observation["truncated"]:
        raise RuntimeError("tracked-file inventory was truncated")
    total = 0
    for item in observation["paths"]:
        path = root / item
        if path.is_file():
            total += path.stat().st_size
    return total


def build_record(
    env: Mapping[str, str] | None = None,
    *,
    now: str | None = None,
    working_tree_bytes: int | None = None,
) -> dict[str, Any]:
    """Build canonical metadata v1 from checkout HEAD via vendored gh_identity."""
    gh = _load_gh_identity()
    root = str(REPO_ROOT)
    wtb = _tracked_bytes(REPO_ROOT) if working_tree_bytes is None else working_tree_bytes
    tooling = gh.tool_versions(_TOOLING_COMMANDS, include_python=True)
    record = gh.repository_metadata_v1(
        root,
        repository=REPOSITORY,
        env=os.environ if env is None else env,
        measurements={"working_tree_bytes": wtb},
        tooling=tooling,
    )
    if now is not None:
        record = dict(record)
        record["generated_at"] = now
        gh.validate_repository_metadata_v1(record)
    return record


def build_payload(
    record: dict[str, Any],
    *,
    probe: bool = True,
    opener: Any = None,
) -> dict[str, Any]:
    """Combine canonical metadata with anonymous resolver evidence (``diagnostics_payload`` plus this page's schema keys)."""
    kwargs: dict[str, Any] = {"probe": probe}
    if opener is not None:
        kwargs["opener"] = opener
    return {"schema_version": "1.0", **diagnostics_payload(record, **kwargs), "generated_at": record["generated_at"]}


def _renderer_page() -> str:
    """The shared diagnostics page from PySaiyanWeBoo (bundled CSS and renderer embedded, nothing from a CDN)."""
    import pysaiyanweboo  # the pinned git dependency in the test extra

    # The pinned diagnostics card uses a light surface. Select the shared light
    # theme so inherited text remains readable without forking vendor CSS.
    return pysaiyanweboo.repository_diagnostics_page(REPOSITORY, theme="github-like", icon=None, favicon=None,
                                              description="Canonical public metadata plus anonymous GitHub URL observations.")


def write_pages(
    out_dir: Path,
    *,
    probe: bool = True,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Write JSON, JSONL, and the reusable diagnostics page into a Pages tree."""
    out_dir.mkdir(parents=True, exist_ok=True)
    record = build_record(env)
    payload = build_payload(record, probe=probe)

    (out_dir / JSON_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / JSONL_NAME).write_text(
        _metadata_jsonl(record),
        encoding="utf-8",
    )
    (out_dir / PAGE_NAME).write_text(_renderer_page(), encoding="utf-8")
    return payload


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    target = Path(argv[0]) if len(argv) > 0 else REPO_ROOT / "_site"
    write_pages(target)


if __name__ == "__main__":
    raise SystemExit(main())
