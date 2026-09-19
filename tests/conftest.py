"""Reset the in-process Wikipedia extract cache between tests.

``load_wikipedia_article`` reuses a process-wide TTL/LRU cache so heading
switches do not refetch. Tests that mock urlopen would otherwise leak
articles across cases (a later Yokohama fetch could hit the previous
test's payload). Default ``pytest -q`` stays network-free.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_wikipedia_article_cache() -> None:
    from mcp_toolcall_lab.wikipedia_tool import reset_article_cache

    reset_article_cache()
    yield
    reset_article_cache()
