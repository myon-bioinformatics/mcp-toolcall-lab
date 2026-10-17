"""Retired upstream source paths must not remain in the vendor lock."""
import json
from pathlib import Path


def test_ironmate_sources_are_not_locked_anymore():
    root = Path(__file__).resolve().parents[1]
    entries = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))["files"]
    ironmate = [e for e in entries if e["repository"] == "myon-bioinformatics/Ironmate"]
    assert ironmate == []
