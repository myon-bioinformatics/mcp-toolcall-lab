"""scripts/stub_pages_smoke.py -- no Docker, no network in default pytest."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import stub_pages_smoke as smoke  # noqa: E402


def test_cpu_completion_ok_reads_choices(monkeypatch) -> None:
    monkeypatch.setattr(
        smoke,
        "_post_json",
        lambda *a, **kw: (200, {"choices": [{"message": {"content": "pong"}}]}),
    )
    ok, detail = smoke._cpu_completion_ok()
    assert ok is True
    assert detail == ""


def test_cpu_completion_ok_false_on_non_200(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_post_json", lambda *a, **kw: (500, "boom"))
    ok, detail = smoke._cpu_completion_ok()
    assert ok is False
    assert "boom" in detail


def test_cpu_completion_ok_false_on_missing_choices(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_post_json", lambda *a, **kw: (200, {"choices": []}))
    ok, detail = smoke._cpu_completion_ok()
    assert ok is False
    assert "no choices" in detail


def test_default_backend_is_lite_stub() -> None:
    assert smoke.CPU_LLM_BACKEND == "lite-stub"
