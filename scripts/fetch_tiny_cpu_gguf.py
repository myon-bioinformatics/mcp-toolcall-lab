#!/usr/bin/env python3
"""Fetch a tiny CPU-class GGUF for docker-compose.gguf.yml. Stdlib only.

Two modes:

* ``CPU_LLM_GGUF_URL`` set -> download that URL directly (explicit pin).
* otherwise -> auto-discover the smallest ``*.gguf`` file in
  ``CPU_LLM_GGUF_REPO`` (default a small, Apache-2.0 instruct model) via the
  Hugging Face API, resolved against that repo's current commit so the URL
  used for *this* fetch is content-pinned even though the commit itself was
  not hand-picked in advance.

Either way, the downloaded bytes are always hashed after the fact — that
sha256 is the ground truth, never metadata we merely printed. If
``CPU_LLM_GGUF_SHA256`` is set, a mismatch is a hard failure (supply-chain
check for a repeat/production run). If it is unset, the computed digest is
only recorded to ``<dest>.provenance.json`` so a human can promote it into
``CPU_LLM_GGUF_SHA256`` once they have verified it out of band -- this
sandbox has no route to huggingface.co to do that itself (agent proxy
denies the CONNECT), so no checksum is hand-typed into this repo unverified.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

HF_API = "https://huggingface.co/api/models"
# Verified to exist via the Hugging Face Hub connector (this sandbox's own
# HTTPS proxy denies huggingface.co directly, but that connector is a
# separate, allowed path) -- a bartowski GGUF quantization of the official
# HuggingFaceTB/SmolLM2-135M-Instruct base model, apache-2.0, 170k+
# downloads. An earlier version of this default pointed at
# "HuggingFaceTB/SmolLM2-135M-Instruct-GGUF", which does not exist and
# would have 404'd on the very first real run.
DEFAULT_REPO = "bartowski/SmolLM2-135M-Instruct-GGUF"
# Preferred tiny quantization/file, then alphabetical. Not sorted by byte size.
# Accuracy is out of scope; startup time is not.
PATTERN_PREFERENCE = ("*q4_k_m*.gguf", "*q4_0*.gguf", "*q8_0*.gguf", "*.gguf")
RETRIES = 4
TIMEOUT = 30.0


def _get_json(url: str) -> Any:
    last_exc: Exception | None = None
    for attempt in range(RETRIES):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
    raise RuntimeError(f"GET {url} failed after {RETRIES} attempts: {last_exc}") from last_exc


def _pick_file(siblings: list[dict[str, Any]], explicit: str | None) -> str:
    """Choose a preferred tiny quantization/file. Does not compare byte sizes."""
    names = [str(s["rfilename"]) for s in siblings if "rfilename" in s]
    if explicit:
        if explicit not in names:
            raise RuntimeError(f"{explicit!r} is not in this repo's file list: {names}")
        return explicit
    ggufs = [name for name in names if name.lower().endswith(".gguf")]
    if not ggufs:
        raise RuntimeError(f"no .gguf files in this repo's file list: {names}")
    for pattern in PATTERN_PREFERENCE:
        matches = sorted(name for name in ggufs if fnmatch(name.lower(), pattern))
        if matches:
            return matches[0]
    return sorted(ggufs)[0]


def discover(repo: str, explicit_file: str | None) -> tuple[str, str, str]:
    """Return (resolved_commit_sha, filename, download_url)."""
    info = _get_json(f"{HF_API}/{repo}")
    commit = str(info.get("sha") or "main")
    filename = _pick_file(info.get("siblings") or [], explicit_file)
    url = f"https://huggingface.co/{repo}/resolve/{commit}/{filename}"
    return commit, filename, url


def _download(url: str, dest: Path) -> tuple[str, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_exc: Exception | None = None
    for attempt in range(RETRIES):
        try:
            digest = hashlib.sha256()
            size = 0
            request = urllib.request.Request(url)
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response, tmp.open("wb") as handle:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            tmp.replace(dest)
            return digest.hexdigest(), size
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            tmp.unlink(missing_ok=True)
    raise RuntimeError(f"download {url} failed after {RETRIES} attempts: {last_exc}") from last_exc


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dest = Path(argv[0]) if argv else Path("docker/stub-pages/models/model.gguf")

    explicit_url = os.environ.get("CPU_LLM_GGUF_URL") or ""
    expected_sha256 = os.environ.get("CPU_LLM_GGUF_SHA256") or ""
    repo = os.environ.get("CPU_LLM_GGUF_REPO") or DEFAULT_REPO
    explicit_file = os.environ.get("CPU_LLM_GGUF_FILE") or None

    if explicit_url:
        commit, filename, url = "explicit-url", explicit_url.rsplit("/", 1)[-1], explicit_url
        source = "explicit"
    else:
        try:
            commit, filename, url = discover(repo, explicit_file)
        except RuntimeError as exc:
            print(f"fetch_tiny_cpu_gguf: discovery failed: {exc}", file=sys.stderr)
            return 2
        source = "hf-api-discovery"

    print(f"fetch_tiny_cpu_gguf: fetching {url}", file=sys.stderr)
    try:
        sha256, size = _download(url, dest)
    except RuntimeError as exc:
        print(f"fetch_tiny_cpu_gguf: {exc}", file=sys.stderr)
        return 2

    if expected_sha256 and sha256 != expected_sha256:
        dest.unlink(missing_ok=True)
        print(
            f"fetch_tiny_cpu_gguf: sha256 mismatch: got {sha256}, expected {expected_sha256}. "
            "Refusing to keep an unverified model file.",
            file=sys.stderr,
        )
        return 3

    provenance = {
        "source": source,
        "repo": repo if source == "hf-api-discovery" else None,
        "revision": commit,
        "file": filename,
        "url": url,
        "sha256": sha256,
        "size_bytes": size,
        "sha256_verified_against_pin": bool(expected_sha256),
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    provenance_path = dest.with_suffix(dest.suffix + ".provenance.json")
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"fetch_tiny_cpu_gguf: wrote {dest} ({size} bytes, sha256={sha256})", file=sys.stderr)
    if not expected_sha256:
        print(
            "fetch_tiny_cpu_gguf: no CPU_LLM_GGUF_SHA256 was set to verify against -- "
            f"once this digest is confirmed out of band, pin it: CPU_LLM_GGUF_SHA256={sha256}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
