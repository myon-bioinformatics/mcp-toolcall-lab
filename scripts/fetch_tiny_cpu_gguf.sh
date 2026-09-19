#!/bin/sh
# Fetch a tiny CPU GGUF for docker-compose.gguf.yml. Real logic is Python
# (JSON from the Hugging Face API, streamed sha256) -- see fetch_tiny_cpu_gguf.py.
#
#   CPU_LLM_GGUF_URL=...            explicit URL, skips discovery
#   CPU_LLM_GGUF_REPO=owner/name    HF repo to discover a *.gguf in (has a default)
#   CPU_LLM_GGUF_FILE=name.gguf     pick this file instead of the smallest match
#   CPU_LLM_GGUF_SHA256=...         verify after download; unset just records it
set -eu
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$DIR/fetch_tiny_cpu_gguf.py" "$@"
