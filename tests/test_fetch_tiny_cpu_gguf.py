"""scripts/fetch_tiny_cpu_gguf.py -- no real huggingface.co access in tests."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fetch_tiny_cpu_gguf as fetch  # noqa: E402


def test_default_repo_is_the_verified_one() -> None:
    # HuggingFaceTB/SmolLM2-135M-Instruct-GGUF (an earlier guess) does not
    # exist -- confirmed live via the Hugging Face Hub connector, which
    # also confirmed this one does (apache-2.0, a quantization of
    # HuggingFaceTB/SmolLM2-135M-Instruct). Regression guard against
    # reintroducing an unverified repo name as the default.
    assert fetch.DEFAULT_REPO == "bartowski/SmolLM2-135M-Instruct-GGUF"
    assert fetch.DEFAULT_FILE == "SmolLM2-135M-Instruct-Q4_K_M.gguf"
    assert fetch.DEFAULT_REVISION == "09816acd5d99df7be770d85ea30822623dab342c"
    assert fetch.DEFAULT_SHA256 == "2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d"
    assert fetch.DEFAULT_SIZE_BYTES == 105454432


def test_pick_file_prefers_smallest_pattern() -> None:
    siblings = [
        {"rfilename": "model-f16.gguf"},
        {"rfilename": "model-q4_k_m.gguf"},
        {"rfilename": "README.md"},
    ]
    assert fetch._pick_file(siblings, None) == "model-q4_k_m.gguf"


def test_pick_file_honors_explicit_choice() -> None:
    siblings = [{"rfilename": "a.gguf"}, {"rfilename": "b.gguf"}]
    assert fetch._pick_file(siblings, "b.gguf") == "b.gguf"


def test_pick_file_rejects_unknown_explicit_choice() -> None:
    siblings = [{"rfilename": "a.gguf"}]
    try:
        fetch._pick_file(siblings, "missing.gguf")
    except RuntimeError as exc:
        assert "missing.gguf" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_pick_file_raises_when_no_gguf() -> None:
    try:
        fetch._pick_file([{"rfilename": "README.md"}], None)
    except RuntimeError as exc:
        assert "no .gguf" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_discover_resolves_commit_and_filename(monkeypatch) -> None:
    payload = json.dumps(
        {"sha": "abc123", "siblings": [{"rfilename": "tiny-q4_k_m.gguf"}, {"rfilename": "README.md"}]}
    ).encode("utf-8")

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(payload)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    commit, filename, url = fetch.discover("some/repo", None)
    assert commit == "abc123"
    assert filename == "tiny-q4_k_m.gguf"
    assert url == "https://huggingface.co/some/repo/resolve/abc123/tiny-q4_k_m.gguf"


def test_download_computes_sha256_and_writes_provenance(tmp_path, monkeypatch) -> None:
    content = b"not a real gguf, just bytes for the test"
    expected_sha = hashlib.sha256(content).hexdigest()

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(content)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "model.gguf"
    sha256, size = fetch._download("http://example.invalid/model.gguf", dest)
    assert sha256 == expected_sha
    assert size == len(content)
    assert dest.read_bytes() == content


def test_download_retries_then_raises(monkeypatch) -> None:
    calls = {"n": 0}

    def always_fails(request, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        raise URLError("no route")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", always_fails)
    monkeypatch.setattr(fetch, "RETRIES", 2)
    try:
        fetch._download("http://example.invalid/model.gguf", Path("/tmp/does-not-matter.gguf"))
    except RuntimeError as exc:
        assert "failed after 2 attempts" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
    assert calls["n"] == 2


def test_main_verifies_expected_sha256_and_fails_loud(tmp_path, monkeypatch) -> None:
    content = b"tiny model bytes"

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(content)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("CPU_LLM_GGUF_URL", "http://example.invalid/model.gguf")
    monkeypatch.setenv("CPU_LLM_GGUF_SHA256", "0" * 64)
    dest = tmp_path / "model.gguf"
    code = fetch.main([str(dest)])
    assert code == 3
    assert not dest.exists()


def test_main_default_repo_uses_pinned_url_and_default_sha(tmp_path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        seen["url"] = request.full_url
        return _FakeResponse(b"not the real gguf")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("CPU_LLM_GGUF_URL", raising=False)
    monkeypatch.delenv("CPU_LLM_GGUF_REPO", raising=False)
    monkeypatch.delenv("CPU_LLM_GGUF_FILE", raising=False)
    monkeypatch.delenv("CPU_LLM_GGUF_SHA256", raising=False)
    dest = tmp_path / "model.gguf"
    code = fetch.main([str(dest)])
    assert code == 3
    assert not dest.exists()
    assert fetch.DEFAULT_REVISION in seen["url"]
    assert fetch.DEFAULT_FILE in seen["url"]


def test_main_records_digest_when_no_pin_given(tmp_path, monkeypatch) -> None:
    content = b"tiny model bytes"
    expected_sha = hashlib.sha256(content).hexdigest()

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(content)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("CPU_LLM_GGUF_URL", "http://example.invalid/model.gguf")
    monkeypatch.delenv("CPU_LLM_GGUF_SHA256", raising=False)
    dest = tmp_path / "model.gguf"
    code = fetch.main([str(dest)])
    assert code == 0
    assert dest.read_bytes() == content
    provenance = json.loads(dest.with_suffix(".gguf.provenance.json").read_text(encoding="utf-8"))
    assert provenance["sha256"] == expected_sha
    assert provenance["sha256_verified_against_pin"] is False
    assert provenance["source"] == "explicit"
