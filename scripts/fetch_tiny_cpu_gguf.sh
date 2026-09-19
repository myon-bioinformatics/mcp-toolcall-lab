#!/bin/sh
# GPT slice helper: download a tiny CPU GGUF for docker-compose.gguf.yml.
# Default URL is a placeholder — pin a checksum before relying on CI.
set -eu
DEST="${1:-docker/stub-pages/models/model.gguf}"
URL="${CPU_LLM_GGUF_URL:-}"
if [ -z "$URL" ]; then
  echo "set CPU_LLM_GGUF_URL to a small GGUF (accuracy does not matter)" >&2
  exit 2
fi
mkdir -p "$(dirname "$DEST")"
curl -fsSL --retry 4 -o "$DEST" "$URL"
echo "wrote $DEST"
