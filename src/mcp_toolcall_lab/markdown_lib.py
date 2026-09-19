"""Load the vendored stdlib ``markdown.py`` without making it a package import.

The sibling repo ships one file. We keep a snapshot under ``vendor/`` so
Docker / GitHub Pages builds stay offline after checkout. If the file is
missing, callers fall back to the stub's own ATX splitter.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]


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
