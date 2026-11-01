# Optional, pinned SDK-only caller environment

This opt-in recipe keeps the original `chat_sim.send_direct` and `send_via_chat`
functions, real MCP 2.3.0/Pydantic 2.14.1 and their locked transitive dependencies.
Only FastMCP and dependencies made unnecessary by its removal are pruned from
the **caller**. A real MCP server, including its separate dependencies, remains
necessary. This does not replace the server, model, UI, or complete test suite.

The repository's manifests, dependency versions, tests, CI and vendor
registrations are unchanged. This guide does not run a benchmark automatically.
The examples below use two deterministic local mock calls, not Wikipedia,
pixiv, a model, Docker, or a browser.

## 1. Stage verified tools and the complete source snapshot

Use an existing Linux/POSIX shell, Git, Python 3.12+, uv 0.13.4 and `sha256sum`.
No runtime/tool is automatically installed or upgraded. The new private
workspace is separate from your working checkout; do not substitute `main`
for these immutable revisions.

The common exporter is pinned to actual parent merge
[`3128b4019b9949a6ccc80c686cd84c93f04af119`](https://github.com/myon-bioinformatics/myon-bioinformatics/commit/3128b4019b9949a6ccc80c686cd84c93f04af119).
It verifies two input manifest blobs, **not checkout HEAD or ancestry**. The
separate Git checks below establish the complete source revision. Recheck
those source files if you edit them later.

Set `TMPDIR` to a writable directory outside every repository checkout. Relative
paths are resolved before changing directory; paths containing spaces are quoted.

```sh
set -eu
umask 077
PYTHON=$(command -v python3)
PYTHON=$("$PYTHON" -S -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$PYTHON")
UV=$("$PYTHON" -S -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$(command -v uv)")
GIT=$("$PYTHON" -S -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$(command -v git)")
TMPDIR=$("$PYTHON" -S -c 'import os; print(os.path.abspath(os.environ.get("TMPDIR") or "/tmp"))')
export TMPDIR
"$PYTHON" -S -c 'import sys; assert sys.version_info >= (3, 12)'
test "$("$UV" --version)" = 'uv 0.13.4'
WORK=$(mktemp -d "${TMPDIR:-/tmp}/mcp-sdk-caller-XXXXXXXX")
WORK=$(cd "$WORK" && pwd -P)
export WORK PYTHON UV GIT
cd "$WORK"

snapshot() {
  directory=$1 repository=$2 revision=$3
  "$GIT" init -q "$directory"
  "$GIT" -C "$directory" remote add origin "https://github.com/$repository.git"
  "$GIT" -C "$directory" fetch --depth=1 --no-tags origin "$revision"
  test "$("$GIT" -C "$directory" rev-parse FETCH_HEAD)" = "$revision"
  "$GIT" -C "$directory" -c core.autocrlf=false checkout -q --detach "$revision"
  test "$("$GIT" -C "$directory" rev-parse HEAD)" = "$revision"
  "$GIT" -C "$directory" diff --exit-code HEAD --
}

snapshot "$WORK/parent" myon-bioinformatics/myon-bioinformatics \
  3128b4019b9949a6ccc80c686cd84c93f04af119
(cd "$WORK/parent" && sha256sum -c <<'HASHES'
aa74cde16da7a33c4cc1398f78c4d0447179ea752d262e8592a11ddb8ce8c3e8  tools/nano_export.py
6b71651b3f361bc54bd921b557d3fc347bbd13e0b98035896c7fa11f2190e535  docs/stdlib-shrink-queue.json
HASHES
)

mkdir "$WORK/src" "$WORK/prepared"
snapshot "$WORK/src/mcp-toolcall-lab" myon-bioinformatics/mcp-toolcall-lab \
  06cdc8e4e14d50de45c8b8122a5ccff6911c3404
test "$("$GIT" hash-object "$WORK/src/mcp-toolcall-lab/pyproject.toml")" = \
  4c3d89820b99fd11717ce0c454e0149226e8d8cd
test "$("$GIT" hash-object "$WORK/src/mcp-toolcall-lab/uv.lock")" = \
  6fd1a4b3a927120d9684d5d3a4e3941de7b13415
"$PYTHON" -S "$WORK/parent/tools/nano_export.py" \
  --feature mcp-chat-client --uv "$UV" \
  --source "$WORK/src/mcp-toolcall-lab" --output-parent "$WORK/prepared" \
  > "$WORK/preparation.json"
FEATURE_DIR=$("$PYTHON" -S -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["directory"])' \
  "$WORK/preparation.json")
export FEATURE_DIR
cat "$FEATURE_DIR/receipt.json"
printf 'Workspace: %s\n' "$WORK"
```

Preparation uses locked/offline export, no extras/default groups/project install,
and `--prune fastmcp`. It validates the reviewed 30 requirement rows/names; these
are **not installed distribution counts**. It installs nothing and starts no
listener. Different manifests or closure fail closed and require explicit
review, not a manually edited receipt.

Keep the full `src/mcp_toolcall_lab` tree. Its lazy server import permits this
caller, but the unchanged initializer imports catalog/Wikipedia/pixiv helpers,
and `chat_sim` imports `record`/`mock.common`. Copying only `chat_sim.py` or
replacing `__init__.py` would not reproduce this source boundary. The recipe
uses `PYTHONPATH` rather than an editable installation that would reinstall
the full project dependencies.

## 2. Explicit caller installation

This optional step downloads the existing hash-fixed binary wheels from
official PyPI into a new venv. It does not provision Python or install the
project. Stop on a failed/unavailable wheel; do not remove hashes, upgrade,
switch registries or introduce a source-build fallback.

```sh
"$PYTHON" -S - "$FEATURE_DIR" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
r = json.loads((p / 'receipt.json').read_text())
assert r['recipe'] == 'mcp-chat-client'
assert r['pruned_packages'] == ['fastmcp']
assert hashlib.sha256((p / 'requirements.txt').read_bytes()).hexdigest() == r['requirements_sha256']
PY
"$PYTHON" -m venv --without-pip "$WORK/caller-venv"
mkdir "$WORK/installer-home" "$WORK/installer-tmp" "$WORK/caller-cache"
env -i PATH="$PATH" HOME="$WORK/installer-home" TMPDIR="$WORK/installer-tmp" \
  UV_CACHE_DIR="$WORK/caller-cache" UV_HTTP_RETRIES=0 UV_HTTP_TIMEOUT=60 \
  "$UV" --no-config pip install --python "$WORK/caller-venv/bin/python" \
  --index-url https://pypi.org/simple --require-hashes --only-binary :all: \
  --no-deps --link-mode copy -r "$FEATURE_DIR/requirements.txt"
```

## 3. Keep the real server separate

If a compatible local fixture server already exists, retain its full environment
and use its loopback endpoint; the caller venv cannot serve FastMCP. Otherwise,
the following optional setup exports the **unpruned default server runtime**
from the same source/lock, with no test/Galleria/browser extras or project install.
This is additional server capacity, not dependency elimination.

```sh
mkdir "$WORK/server-cache" "$WORK/server-home" "$WORK/server-tmp"
env -i PATH="$PATH" HOME="$WORK/installer-home" TMPDIR="$WORK/installer-tmp" \
  UV_CACHE_DIR="$WORK/server-cache" \
  "$UV" --no-config export --project "$WORK/src/mcp-toolcall-lab" \
  --offline --locked --no-python-downloads --python "$PYTHON" \
  --no-default-groups --no-emit-project --no-header --format requirements.txt \
  > "$WORK/server-requirements.txt"
"$PYTHON" -m venv --without-pip "$WORK/server-venv"
env -i PATH="$PATH" HOME="$WORK/installer-home" TMPDIR="$WORK/installer-tmp" \
  UV_CACHE_DIR="$WORK/server-cache" UV_HTTP_RETRIES=0 UV_HTTP_TIMEOUT=60 \
  "$UV" --no-config pip install --python "$WORK/server-venv/bin/python" \
  --index-url https://pypi.org/simple --require-hashes --only-binary :all: \
  --no-deps --link-mode copy -r "$WORK/server-requirements.txt"
```

Start the unchanged package server in the foreground in terminal A. Use an
available local port; if 8765 is busy, stop and choose another port explicitly
in both terminal commands. Do not fall back to a public listener.

```sh
env -i PATH="$PATH" HOME="$WORK/server-home" TMPDIR="$WORK/server-tmp" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$WORK/src/mcp-toolcall-lab/src" \
  MCP_HOST=127.0.0.1 MCP_PORT=8765 MCP_TOOLCALL_LOG="$WORK/server-calls.jsonl" \
  "$WORK/server-venv/bin/python" -m mcp_toolcall_lab.server
```

The server exposes live Wikipedia/pixiv tools as well as three mocks. The caller
example below names only `find_municipalities`; **do not call live-source tools**
as part of this local example. A loopback listener and a venv are not an egress
firewall/security sandbox. No credentials or models are required for these mocks.

## 4. Call the unchanged SDK functions

In terminal B, set `WORK` to the exact workspace path printed in step 1 (do not
create a second workspace). The endpoint must match terminal A. This exercises
the real MCP session/serialization, not a simulated SDK or a real chat UI.

```sh
cd "$WORK"
mkdir "$WORK/caller-home" "$WORK/caller-tmp"
env -i PATH="$PATH" HOME="$WORK/caller-home" TMPDIR="$WORK/caller-tmp" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$WORK/src/mcp-toolcall-lab/src" \
  "$WORK/caller-venv/bin/python" - <<'PY'
import asyncio, json
from mcp_toolcall_lab.chat_sim import send_direct, send_via_chat

async def main():
    url = 'http://127.0.0.1:8765/mcp'
    direct = await send_direct(url, tool_name='find_municipalities',
                               arguments={'query': 'Exampleville'})
    chat = await send_via_chat(url, user_text='Find Exampleville',
                               tool_name='find_municipalities',
                               arguments={'query': 'Exampleville'},
                               chat_id='chat_nano_example', call_id='call_nano_example')
    assert not direct.is_error and not chat.mcp_result.is_error
    print(json.dumps({'direct': direct.model_dump(mode='json'),
                      'chat_result': chat.mcp_result.model_dump(mode='json'),
                      'tool_message': chat.tool_result_message}, ensure_ascii=False))

asyncio.run(asyncio.wait_for(main(), timeout=30))
PY
```

Stop terminal A with Ctrl-C when finished. Retain its local log if needed; do
not publish arbitrary tool inputs/logs. This example is usage, not a newly timed
equivalence/parallelism benchmark or proof of all tool behavior.

## Scope, accounting and the original tests

The historical [comparison and source-pinned evidence](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/3128b4019b9949a6ccc80c686cd84c93f04af119/docs/nano-client-mirror-environments.md)
observed 69 default versus 28 caller distributions on its Linux/Python host.
The fixture server still required the full 69-distribution environment. Two
separate full-server/thin-caller venvs used more apparent disk than one shared
full venv: **89,205,885 versus 58,981,931 bytes**. Source, caches and shared
interpreter costs were accounted separately. Do not claim whole-host savings,
new installed counts on another platform, or a general speed improvement.

Keep the [normal tests and optional experiments](../README.md#tests-and-optional-experiments)
in their full environments. The caller does not contain FastMCP/test extras or
all integrations; no existing test is deleted, weakened or newly skipped by this
guide. The prior bounded result is not proof that this complete child suite ran
in the thin environment. This docs-only adoption neither changes CI nor enrolls
a vendor, starts manual workflows, publishes Pages, or merges the child.
