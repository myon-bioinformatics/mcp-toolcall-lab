"""Tiny shared helper so every tests/e2e/ module skips the same way when its
target UI isn't up — none of these lanes should error out CI, they should be
inert until a maintainer runs docker-compose.yml (see docs/e2e_foundation.md).
"""

from __future__ import annotations

import httpx


def is_reachable(url: str, *, timeout: float = 2.0) -> bool:
    try:
        httpx.get(url, timeout=timeout)
        return True
    except httpx.TransportError:
        return False
