"""Screenshot + observation path for the LibreChat Docker smoke."""

from __future__ import annotations

from pathlib import Path

import pytest

RESULTS_DIR = Path(__file__).resolve().parents[2] / "test-results"


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(autouse=True)
def _screenshot_on_failure(request):
    yield
    failed = getattr(request.node, "rep_call", None)
    if not (failed is not None and failed.failed):
        return
    page = request.node.funcargs.get("page")
    if page is None:
        return
    RESULTS_DIR.mkdir(exist_ok=True)
    safe_name = request.node.name.replace("/", "_")
    try:
        page.screenshot(path=str(RESULTS_DIR / f"{safe_name}.png"), full_page=True)
    except Exception:
        pass
