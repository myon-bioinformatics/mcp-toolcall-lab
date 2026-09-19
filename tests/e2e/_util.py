"""Small helpers shared by the chat-e2e / browser-smoke test modules.

Deliberately has no Playwright import: these two functions are useful even
for a module that hasn't reached the point of launching a browser yet (they
decide whether it's worth launching one at all).
"""

from __future__ import annotations

import os

import httpx
import pytest


def base_url(env_var: str, default: str) -> str:
    """Read a lane's target URL from the environment, e.g. set by `docker compose`.

    Defaults to the port this repo's own docker-compose.yml publishes for that
    service on localhost, so the lane also works run straight from the host
    against `docker compose up`.
    """
    return os.environ.get(env_var, default)


def require_reachable(url: str, label: str) -> None:
    """Skip the test (not fail it) when `label`'s container isn't up.

    Keeps this lane's tests inert -- rather than hanging or erroring -- when
    nobody ran `docker compose up` first, the same way
    tests/test_browser_fetch_protocol.py's `pytest.importorskip` keeps that
    module inert when playwright isn't installed.
    """
    try:
        httpx.get(url, timeout=2.0)
    except httpx.TransportError:
        pytest.skip(f"{label} is not reachable at {url} -- run `docker compose up {label}` first")
