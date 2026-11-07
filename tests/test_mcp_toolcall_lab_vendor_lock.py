import hashlib, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_vendor_lock_matches_checked_in_bytes() -> None:
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    assert set(lock) == {"schema", "files"}
    assert lock["schema"] == "vendor-lock/1"
    for entry in lock["files"]:
        assert re.fullmatch(r"[0-9a-f]{40}", entry["ref"]), entry["destination"]
        assert entry["ref"] == entry["commit"], entry["destination"]
        data = (ROOT / entry["destination"]).read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        assert blob == entry["blob_sha"], entry["destination"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["destination"]


def test_pins_hold_digest_and_revisions_for_budget_workflow() -> None:
    runtime_lock = json.loads((ROOT / "experiments/llm-runtime.lock.json").read_text(encoding="utf-8"))
    assert set(runtime_lock) == {"schema", "pins"}
    assert runtime_lock["schema"] == "llm-runtime-lock/1"
    pins = runtime_lock["pins"]
    assert re.fullmatch(r"ghcr\.io/ggml-org/llama\.cpp@sha256:[0-9a-f]{64}", pins["llama_image"])
    assert re.fullmatch(r"[0-9a-f]{40}", pins["llama_revision"])
    assert re.fullmatch(r"[0-9a-f]{40}", pins["baseline_revision"])
    workflow = (ROOT / ".github/workflows/budget-quality-gguf.yml").read_text(encoding="utf-8")
    assert 'json.load(open("experiments/llm-runtime.lock.json", encoding="utf-8"))["pins"]' in workflow
    assert "vendor.lock.json" not in workflow
    assert "PYTHONPATH: src" in workflow
    assert "steps.pins.outputs.llama_image" in workflow
    assert "steps.pins.outputs.llama_revision" in workflow
    assert "steps.pins.outputs.baseline_revision" in workflow
    assert "LLAMA_IMAGE:" not in workflow and "BASELINE:" not in workflow
