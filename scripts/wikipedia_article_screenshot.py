#!/usr/bin/env python3
"""Screenshot the stdlib stub's /wiki form with Playwright's own CLI.

Default path is deterministic: MediaWiki fixture, no live Wikipedia.

    python scripts/wikipedia_article_screenshot.py --out test-results/wiki-screenshots

Live Wikipedia is an explicit opt-in (same gate as the unit tests):

    MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA=1 python scripts/wikipedia_article_screenshot.py --live

This is not part of ``pytest -q``. The dedicated workflow
``.github/workflows/wikipedia-article-screenshot.yml`` runs it.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "fixtures" / "wikipedia" / "yokohama_extract.json"
DEFAULT_TITLE = "Yokohama"
DEFAULT_HEADING = "Geography"
LIVE_ENV = "MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mcp_toolcall_lab.stub_front import DEFAULT_CORPUS, StubState, load_corpus, make_handler  # noqa: E402
from mcp_toolcall_lab.wikipedia_tool import FIXTURE_ENV, reset_article_cache  # noqa: E402


def screenshot_argv(url: str, path: Path) -> list[str]:
    """``python -m playwright screenshot`` — the CLI, not a hand-rolled client."""
    return [
        sys.executable,
        "-m",
        "playwright",
        "screenshot",
        "--full-page",
        url,
        str(path),
    ]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, timeout: float = 8.0) -> None:
    from urllib.error import URLError
    from urllib.request import urlopen

    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(0.05)
    raise RuntimeError(f"stub never became ready at {url}: {last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="test-results/wiki-screenshots")
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--heading", default=DEFAULT_HEADING)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch live Wikipedia instead of the pinned fixture (also honours "
        f"{LIVE_ENV}=1).",
    )
    args = parser.parse_args(argv)

    live = args.live or os.environ.get(LIVE_ENV) == "1"
    if live:
        os.environ.pop(FIXTURE_ENV, None)
    else:
        fixture = Path(args.fixture)
        if not fixture.is_file():
            print(f"fixture not found: {fixture}", file=sys.stderr)
            return 2
        os.environ[FIXTURE_ENV] = str(fixture)

    reset_article_cache()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    title_path = out_dir / "wiki-title-entered.png"
    heading_path = out_dir / "wiki-heading-selected.png"

    port = _free_port()
    state = StubState(sections=load_corpus(DEFAULT_CORPUS), mcp_url=None)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_for_http(f"{base}/wiki")
        title_url = f"{base}/wiki?{urlencode({'title': args.title})}"
        heading_url = (
            f"{base}/wiki?{urlencode({'title': args.title, 'heading': args.heading})}"
        )
        for url, path in ((title_url, title_path), (heading_url, heading_path)):
            result = subprocess.run(screenshot_argv(url, path), check=False, capture_output=True, text=True)
            if result.returncode != 0:
                print(result.stdout, end="")
                print(result.stderr, file=sys.stderr, end="")
                print(
                    "Playwright CLI screenshot failed. Install with:\n"
                    "  pip install -e '.[browser-test]'\n"
                    "  playwright install chromium",
                    file=sys.stderr,
                )
                return result.returncode
            print(path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
