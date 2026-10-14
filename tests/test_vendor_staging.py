"""Vendor evidence staging was removed when vendor.lock.json became the sole record."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workflows_no_longer_invoke_vendor_stage():
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        assert "Stage canonical vendor evidence" not in text
        assert "vendor_stage.py" not in text
        assert "vendor-promotion.json" not in text
