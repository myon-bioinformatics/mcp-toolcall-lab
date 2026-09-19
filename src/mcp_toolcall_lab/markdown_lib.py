"""Load the vendored stdlib ``markdown.py`` without making it a package import.

The sibling repo ships one file. We keep a snapshot under ``vendor/`` so
Docker / GitHub Pages builds stay offline after checkout. If the file is
missing, callers fall back to the stub's own ATX splitter.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVENANCE_PATH = REPO_ROOT / "vendor" / "markdown.provenance.json"


def markdown_py_path() -> Path | None:
    override = os.environ.get("MARKDOWN_PY")
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.append(REPO_ROOT / "vendor" / "markdown.py")
    candidates.append(Path("/app/vendor/markdown.py"))
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_markdown() -> ModuleType | None:
    path = markdown_py_path()
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("lab_vendored_markdown", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_provenance() -> dict[str, str]:
    return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))


def assert_markdown_provenance(path: Path | None = None) -> dict[str, str]:
    """Fail if the vendored file drifted from the recorded commit/blob."""
    recorded = load_provenance()
    target = path or markdown_py_path()
    if target is None:
        raise FileNotFoundError("vendor/markdown.py is missing")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    blob = git_blob_sha(target)
    if blob != recorded["blob_sha"] or digest != recorded["sha256"]:
        raise ValueError(
            f"vendored markdown.py does not match {PROVENANCE_PATH.name}: "
            f"blob={blob} sha256={digest} recorded={recorded}"
        )
    return recorded
