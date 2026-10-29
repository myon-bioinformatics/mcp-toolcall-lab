# Build-time runtimes only. The final target contains static files, not a service.
FROM ghcr.io/ggml-org/llama.cpp:server-b11669@sha256:8ffe0d28ff5261cc2f78ca1f67db1515bc2529b904a3c25f5b24f4cedfc3411d AS llama
FROM node:26.12.1-trixie-slim@sha256:36793bcbb160a6a16cede1e62b93f84e26b78a972a8a4c0b879c1c12f22761ec AS node
FROM python:3.15.1-slim-trixie@sha256:08275f764e022331c6d3e3ca55d8cce01fa4684b02111adf0445d71806fe7941 AS build
# Ordinary signed Debian security update; no custom CA trust overrides.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates=20250419+deb13u1 libstdc++6=14.2.0-19 libatomic1=14.2.0-19 libgomp1=14.2.0-19 \
    && rm -rf /var/lib/apt/lists/*
# Hash-pinned bootstrap from the Python security advisory (see docs/software-components.md).
RUN python -m pip install --no-cache-dir --no-deps \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=llama /app/ /app/
WORKDIR /source
# The pinned image places shared llama/server libraries beside the executable.
ENV LD_LIBRARY_PATH=/app
# Fail before downloading the model, preserving loader diagnostics in CI.
RUN /app/llama-server --version
COPY scripts/fetch_tiny_cpu_gguf.py ./scripts/
# Existing commit/size/SHA-pinned public model. No credentials or inference API.
RUN python3 -S scripts/fetch_tiny_cpu_gguf.py /models/model.gguf
COPY src ./src
COPY vendor ./vendor
COPY vendor.lock.json LICENSE ./
COPY scripts/build_portable_pages.py scripts/write_portfolio_redirect.py scripts/check_incremental_summary_build.py ./scripts/
COPY fixtures/portable_summary ./fixtures/portable_summary
COPY docs/portable-execution.md docs/build-time-summaries.md docs/summary-corpus.md ./docs/
COPY docs/assets/mcp-toolcall-lab-logo.png docs/assets/mcp-toolcall-lab-favicon-artwork.png ./docs/assets/
ARG SOURCE_SHA
ARG BUILD_RUN
ARG BUILD_ATTEMPT
ARG REVIEWED_CORPUS
ARG CASE_OFFSET=0
ARG CASE_LIMIT=16
ENV GITHUB_RUN_ID=$BUILD_RUN GITHUB_RUN_ATTEMPT=$BUILD_ATTEMPT
ENV PYTHONPATH=/source/src
# The cache is local to this builder, never copied wholesale into public output.
# A fresh hosted runner has no prior cache unless deliberately restored by its owner.
RUN --mount=type=cache,target=/summary-cache \
    if [ -n "$REVIEWED_CORPUS" ]; then set -- --reviewed-corpus "$REVIEWED_CORPUS"; else set --; fi; \
    python3 -S scripts/build_portable_pages.py --out /site --source-sha "$SOURCE_SHA" --model /models/model.gguf --llama-server /app/llama-server --result-cache /summary-cache --case-offset "$CASE_OFFSET" --case-limit "$CASE_LIMIT" "$@"
# Capture only independently reviewed selections and their verified actual Pages receipts.
RUN if [ -n "$REVIEWED_CORPUS" ]; then set -- --reviewed-corpus "$REVIEWED_CORPUS"; else set --; fi; \
    python3 -S scripts/check_incremental_summary_build.py capture --execution /site/execution --out /pages-handoff --source-sha "$SOURCE_SHA" --case-offset "$CASE_OFFSET" --case-limit "$CASE_LIMIT" "$@"
# Prove durable restore in separate processes using only declared synthetic cases.
# This handoff is separate from Pages and contains reviewed-public data only.
RUN python3 -S scripts/check_incremental_summary_build.py check --out /summary-handoff --source-sha "$SOURCE_SHA" --model /models/model.gguf --llama-server /app/llama-server
FROM scratch AS summary-handoff
COPY --from=build /pages-handoff/ /pages/
COPY --from=build /summary-handoff/ /validation/
FROM scratch AS artifact
COPY --from=build /site/ /
