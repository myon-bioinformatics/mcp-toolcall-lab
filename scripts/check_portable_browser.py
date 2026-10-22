"""Exercise exported portable UI on a local static server (Chromium, no backend)."""
from __future__ import annotations
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
from playwright.sync_api import sync_playwright


def check(site: Path, screenshot: Path, executable: str | None = None) -> None:
    # Serve beneath a project prefix, as GitHub project Pages does. Absolute
    # root paths must not accidentally work in this browser regression check.
    temporary = tempfile.TemporaryDirectory()
    shutil.copytree(site, Path(temporary.name) / 'mcp-toolcall-lab')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=temporary.name))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            base = f'http://127.0.0.1:{server.server_port}/mcp-toolcall-lab/execution/'
            page.goto(base)
            logo = page.locator('.brand-logo')
            assert logo.is_visible()
            page.wait_for_function("document.querySelector('.brand-logo').naturalWidth === 460")
            assert logo.evaluate('(img) => img.naturalHeight') == 500
            favicon = page.locator('link[rel=icon]')
            assert favicon.get_attribute('type') == 'image/png'
            assert favicon.get_attribute('sizes') == '180x180'
            meta = page.request.get(base + 'build.json').json()
            for locator, attribute in [(logo, 'src'), (favicon, 'href')]:
                relative = locator.get_attribute(attribute)
                url = locator.evaluate('(element, attribute) => element[attribute]', attribute)
                assert url == base + relative
                response = page.request.get(url)
                assert response.status == 200
                assert response.headers['content-type'].startswith('image/png')
                assert hashlib.sha256(response.body()).hexdigest() == meta['assets'][relative]
            assert page.evaluate('''async () => {
                const icon = new Image();
                icon.src = document.querySelector('link[rel=icon]').href;
                await icon.decode();
                return icon.naturalWidth === 180 && icon.naturalHeight === 180;
            }''')
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('Input ready')")
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'ok · deterministic_log_analysis'")
            result = json.loads(page.locator('#result').inner_text())
            assert result['metrics']['tool_calls'] == 2
            assert result['confidence'] is None
            # Repeated runs and edited inputs cannot keep stale evidence visible.
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'ok · deterministic_log_analysis'")
            page.locator('#request').fill('not JSON')
            assert page.locator('#status').inner_text() == 'Not executed for this input.'
            page.locator('#run').click()
            assert json.loads(page.locator('#result').inner_text())['error']['code'] == 'invalid_input'
            page.locator('#adapter').click()
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('Input ready')")
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'ok · build_artifact'")
            value = json.loads(page.locator('#request').input_value())
            value['input']['selector']['query'] = 'never-built'
            page.locator('#request').fill(json.dumps(value))
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'cache_miss · unsupported'")
            assert json.loads(page.locator('#result').inner_text())['confidence'] is None
            # Clear while a fetch is pending cannot be overwritten by its late answer.
            pending = []
            page.route('**/adapter-request.json', lambda route: pending.append(route))
            page.locator('#adapter').click()
            page.wait_for_timeout(100)
            assert pending, 'Expected a held example fetch'
            page.locator('#clear').click()
            pending[0].fulfill(status=200, content_type='application/json', body=json.dumps(value))
            page.wait_for_timeout(100)
            assert page.locator('#request').input_value() == ''
            assert page.locator('#status').inner_text() == 'Cleared.'
            page.locator('#sample').click()
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('Input ready')")
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'ok · deterministic_log_analysis'")
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert logo.is_visible()
            assert logo.evaluate('(img) => img.naturalWidth') == 460
            page.screenshot(path=str(screenshot.with_name(screenshot.stem + '-mobile.png')), full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        temporary.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, required=True)
    parser.add_argument('--screenshot', type=Path, required=True)
    parser.add_argument('--chromium-path')
    args = parser.parse_args()
    check(args.site, args.screenshot, args.chromium_path)
