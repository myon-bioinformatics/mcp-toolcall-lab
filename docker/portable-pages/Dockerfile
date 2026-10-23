# Build-time runtimes only. The final target contains static files, not a service.
FROM node:24-bookworm-slim AS node
FROM python:3.12-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 libatomic1 && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
WORKDIR /source
COPY src ./src
COPY vendor ./vendor
COPY vendor.lock.json LICENSE ./
COPY scripts/build_portable_pages.py scripts/write_portfolio_redirect.py ./scripts/
COPY docs/portable-execution.md ./docs/
COPY docs/assets/mcp-toolcall-lab-logo.png docs/assets/mcp-toolcall-lab-favicon-artwork.png ./docs/assets/
ARG SOURCE_SHA
ARG BUILD_RUN
ARG BUILD_ATTEMPT
ENV GITHUB_RUN_ID=$BUILD_RUN GITHUB_RUN_ATTEMPT=$BUILD_ATTEMPT
ENV PYTHONPATH=/source/src
RUN python -S scripts/build_portable_pages.py --out /site --source-sha "$SOURCE_SHA"
FROM scratch AS artifact
COPY --from=build /site/ /
