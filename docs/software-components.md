# Software component refresh (2026-10-24)

This document retains the component inventory from the **2026-10-24 refresh**.
The CI version-selection and manual-model policies adopted on **2026-10-31**
are described separately below; they do not rewrite historical test or model
results. The Python package remains compatible with Python **3.12+**, and the
portable runtime with Node **24+**.

## Current CI selection policy (2026-10-31)

- The setup-uv action remains SHA-pinned, while its uv binary selection is
  **`latest`**. The latest-version test and browser-protocol lanes request
  **Python 3.15** through `UV_PYTHON`, paired with Node **26.12.1**. The actual
  patch follows uv's interpreter selection, including available interpreters and
  its download catalog; this is not a guarantee of the newest upstream patch.
- Exact compatibility lanes remain **Python 3.12.15 / Node 24.22.1 and 26.12.1**;
  JSONL archive checks retain **3.12.15 / 3.13.17 / 3.14.9** on all three OSes.
  The standalone public-resolver Python **3.9/3.10** checks are unchanged.
- This floating CI selection is distinct from the immutable image and dependency
  inventory below. All six Python Dockerfiles retain **3.15.1 + digest**.
  Upgrade those pins and run their corresponding checks together.
- Re-running the same commit can now select a newer uv or Python 3.15 patch.
  Use each run's setup logs for the resolved versions. The
  `junit-python-3.15-node-26.12.1` artifact label identifies a minor-series lane,
  not an exact Python patch; its `expected-reports` entry uses the same label.
  The uv security floor remains a minimum, not an exact selection or a substitute
  for checking the resolved runtime.

## Project-owned components

- All six Python Dockerfiles: Python **3.15.1**, Debian trixie, immutable
  multi-architecture digest. Portable builds copy Node **26.12.1** from a
  matching trixie image. Node 26 is stable Current; Node 24 is the LTS baseline.
- Debian **ca-certificates 20250419+deb13u1**, plus portable Node libraries
  **libstdc++6/libatomic1 14.2.0-19**, are explicit signed package pins.
- Every Python image bootstraps **pip 26.2.2** from the Python security team's
  exact wheel URL and SHA-256. No custom roots, TLS bypasses or host trust
  settings are added.
- At the snapshot, workflows pinned **uv 0.13.5**, **Deno 2.9.8**,
  **Playwright 1.64.1**, and then-current stable GitHub Action release commits
  for ordinary build/test workflows. The current uv selection is described above.
  The permissioned Claude automation is an explicit exception: its baseline
  workflow is unchanged because publishing its action update was not authorized
  by the security review. Shared repository/vendor/tool lineage pins also stay
  unchanged. The October 24 refresh preserved the then-existing Pages
  publication path; the October 31 manual-only policy below supersedes that
  earlier automatic-trigger description.
- Direct dependency floors and `uv.lock` refresh FastMCP to **4.1.3**, FastAPI to
  **0.162.0**, Playwright to **1.64.1**, and the build backend to
  **hatchling 1.32.4**. The lock also includes **certifi 2026.10.23** and
  **Brotli 1.2.1**, including the upstream Brotli security fix. All installed
  project packages, including optional test/Galleria/browser dependencies,
  reported no newer stable PyPI version at the snapshot check.

Existing Gradio/Playwright override constraints remain explicit in `pyproject.toml`.
A latest version does not imply upstream metadata supports every override; the
[optional Galleria checks](../README.md#optional-local-galleria) distinguish lock
consistency and exercised compatibility from `uv pip check` metadata warnings.

The MCP Docker images now install the same root dependency closure as the uv
lock, not an independent floating `fastmcp>=...` resolution. Regenerate it with:

```sh
uv export --locked --no-dev --no-emit-project --no-header --no-annotate \
  --format requirements-txt -o docker/runtime-requirements.txt
```

`docker/build-requirements.txt` separately records official PyPI wheel hashes
for **setuptools 84.2.1** and **Cython 3.3.0**. PyYAML 6.0.3 has no CPython 3.15
wheel, so its source build needs those builders. Docker installs their wheels
first, then uses `--no-build-isolation --require-hashes --no-deps` for the runtime
closure. A missing compiler may select PyYAML's supported pure-Python fallback;
it must not cause a new unpinned build environment to be downloaded.

## Upstream service boundaries

| Component | Selected image | Boundary |
| --- | --- | --- |
| MongoDB | 9.0.4 + digest | Fresh disposable smoke data only. Not a supported 7→9 persistent-volume migration. |
| Open WebUI | v0.12.3 + digest | Latest stable upstream service. Its image embeds Python 3.11.17; project runtime policy does not rewrite upstream internals. |
| llama.cpp | v0.7.0, server-b11669 + digest | Stable release, not newer rolling builds. The existing 105,454,432-byte SmolLM2 GGUF, source revision and SHA-256 stay unchanged. |
| LibreChat | current official dev image + digest | Explicit exception: all 98 published releases were marked prerelease. The existing latest-product smoke intent is retained, not mislabeled stable. Official namespace is now librechat-ai; the pinned image embeds Node 24.22.1. |

Application image versions alone do not prove every bundled component or trust
store is current. Upstream Open WebUI, MongoDB and llama.cpp image builds predate
the CA incident response. Their ordinary integration tests exercise the local
mock network, not a complete outbound TLS audit. Do not describe those vendor
images, a live deployment or user host as remediated by this change.

## Verification and operational limits

The following workflow policy is current as of **2026-10-31**.

Automatic `component-runtime.yml` PR checks rebuild **five non-model images**
with `--pull --no-cache`, check the lock export, and record versions plus live
TLS receipts from the actual containers. They test the Python system store,
certifi, pip's vendored/mixed store and the installed MCP client's HTTPX2 factory
when present. The browser-protocol job also remains automatic.
See [runtime-trust.md](runtime-trust.md) for exact pass/failure semantics.
Timeouts, blocked probes and missing required clients are not evidence of
distrust. A metadata-only receipt is not a security pass.

The sixth, portable-build image generates real model output. Its complete
build/trust checks remain in the separate `portable-live` job, guarded by
`workflow_dispatch`; ordinary PRs report that job as **skipped**, not passed.
`portable-pages.yml`, `compaction-gguf-benchmark.yml` and `budget-quality-gguf.yml`
also remain **manual-only**. Their real generation, model measurements and
validation commands are retained, not replaced with mock successes. Portable
browser validation remains part of the manual portable workflow.

The sole Pages publisher is `portable-pages.yml`. Both its Pages artifact upload
and deployment require a manual dispatch on `main`. Ordinary PRs and pushes do
not regenerate or replace the published Pages content. Adopting floating CI
versions does not itself authorize a model run or manual publication.

Core compatibility/latest tests, Galleria, the browser-protocol lane and existing
LibreChat/Open WebUI/stub mock-backed smokes remain automatic. The October 24
refresh's broader validation scope included the portable image and live GGUF
benchmark; those historical results do not establish that these manual stages
ran again under a later CI runtime. Report failed, skipped and not-run stages
separately. Receipts apply to the exact tested commit, resolved runtimes and
rebuilt images, not pre-existing containers. No host security change, custom CA
edit or persistent data migration is part of this CI selection policy.

## Primary sources

- [Python image tags/digest](https://hub.docker.com/v2/repositories/library/python/tags/3.15.1-slim-trixie)
- [Node image tags/digest](https://hub.docker.com/v2/repositories/library/node/tags/26.12.1-trixie-slim)
- [MongoDB image tags/digest](https://hub.docker.com/v2/repositories/library/mongo/tags/9.0.4)
- [Open WebUI v0.12.3](https://github.com/open-webui/open-webui/releases/tag/v0.12.3)
- [llama.cpp v0.7.0](https://github.com/ggml-org/llama.cpp/releases/tag/v0.7.0)
- [LibreChat official compose](https://github.com/LibreChat-AI/LibreChat/blob/94ae8a419839f8c99f5cfc15a650804ced8163c2/docker-compose.yml)
- [FastMCP security notice](https://github.com/PrefectHQ/fastmcp/blob/main/SECURITY.md#certificate-authority-incident-october-2026)
- [Python security team's installer guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)
- [Debian fixed CA package](https://packages.debian.org/trixie/ca-certificates)
- [Brotli 1.2.1 security release](https://github.com/google/brotli/releases/tag/v1.2.1)
- [PyYAML's declared source builders](https://github.com/yaml/pyyaml/blob/6.0.3/pyproject.toml)
- [PyPI release metadata](https://pypi.org/) supplies package versions and distribution hashes recorded in the lockfiles.
