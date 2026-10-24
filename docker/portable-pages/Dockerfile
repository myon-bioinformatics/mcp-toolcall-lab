# Build-time runtimes only. The final target contains static files, not a service.
FROM node:24-bookworm-slim AS node
FROM ghcr.io/ggml-org/llama.cpp:server@sha256:9002e60256635720c05c50455fdebf992a98dc25466dc932758ecd7936244c1c AS build
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates libstdc++6 libatomic1 && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
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
