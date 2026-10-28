# Build-time runtimes only. The final target contains static files, not a service.
FROM node:26.12.1-trixie-slim@sha256:36793bcbb160a6a16cede1e62b93f84e26b78a972a8a4c0b879c1c12f22761ec AS node
FROM python:3.15.1-slim-trixie@sha256:08275f764e022331c6d3e3ca55d8cce01fa4684b02111adf0445d71806fe7941 AS build
# Ordinary signed Debian security update; no custom CA trust overrides.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates=20250419+deb13u1 libstdc++6=14.2.0-19 libatomic1=14.2.0-19 libgomp1=14.2.0-19 \
    && rm -rf /var/lib/apt/lists/*
# Hash-pinned bootstrap from the Python security advisory (see docs/software-components.md).
RUN python -m pip install --no-cache-dir --no-deps \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
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
