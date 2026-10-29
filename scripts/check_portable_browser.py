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


def check(site: Path, screenshot: Path, executable: str | None = None, *, require_generated: bool = False) -> None:
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
            # Legacy trace controls live inside a collapsed disclosure. Exercise
            # the real visible control rather than forcing clicks on hidden UI.
            page.locator('#advanced > summary').click()
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
            # Input-first source selection never sends pasted material over the network.
            page.locator('#advanced > summary').click()
            requests = []
            page.on('request', lambda request: requests.append(request.url))
            source = 'Graphs connect nodes. Graphs support routing. 原文を選択します。 <img src=x onerror=alert(1)>'
            page.locator('#source-text').fill(source)
            page.locator('#source-extract').click()
            page.wait_for_function("document.querySelector('#source-status').textContent.includes('candidate sentences')")
            initial = json.loads(page.locator('#source-notes').text_content())
            assert initial['model_inference_performed'] is False
            assert initial['confidence'] is None
            keyword = next(item['id'] for item in initial['keyword_options'] if item['text'] == 'Graphs')
            page.locator('#keyword-options').select_option(keyword)
            page.wait_for_function("document.querySelector('#source-status').textContent.startsWith('2 candidate sentences')")
            page.locator('#sentence-options input').first.check()
            page.wait_for_function("document.querySelector('#source-status').textContent.includes('1 selected')")
            selected = json.loads(page.locator('#source-notes').text_content())
            assert selected['summary_request']['generated_text'] is None
            assert page.locator('#summary-download').is_enabled()
            assert page.locator('#selected-evidence').inner_text() == 'Graphs connect nodes.'
            assert page.locator('#sentence-options img').count() == 0
            with page.expect_download() as download_info:
                page.locator('#summary-download').click()
            payload = json.loads(Path(download_info.value.path()).read_text())
            assert payload == selected['summary_request']
            with page.expect_download() as case_info:
                page.locator('#source-case-download').click()
            case_payload = json.loads(Path(case_info.value.path()).read_text())
            assert case_payload['operation'] == 'source_selection'
            assert case_payload['input']['text'] == source
            assert case_payload['input']['sentence_ids'] == [selected['selected_sentences'][0]['id']]
            # Edited input, repeated extraction and Clear discard all old evidence.
            page.locator('#source-text').fill('Different input.')
            assert page.locator('#keyword-options').is_disabled()
            assert page.locator('#summary-download').is_disabled()
            assert page.locator('#source-case-download').is_disabled()
            assert page.locator('#selected-evidence').inner_text() == ''
            assert page.locator('#sentence-options input').count() == 0
            page.locator('#source-extract').click()
            page.locator('#source-clear').click()
            page.wait_for_timeout(100)
            assert page.locator('#source-status').inner_text() == 'Source cleared.'
            assert page.locator('#keyword-options option').count() == 0
            page.locator('#source-extract').click()
            page.wait_for_function("document.querySelector('#source-status').textContent.startsWith('No source text')")
            assert page.locator('#summary-download').is_disabled()
            page.locator('#source-text').fill('x' * (128 * 1024 + 1))
            page.locator('#source-extract').click()
            page.wait_for_function("document.querySelector('#source-notes').textContent.includes('invalid_input')")
            assert page.locator('#summary-download').is_disabled()
            page.locator('#source-text').fill('Graphs connect nodes. Graphs support route analysis. Source sentences remain inspectable.')
            for _ in range(2):
                page.locator('#source-extract').click()
                page.wait_for_function("document.querySelector('#source-status').textContent.includes('candidate sentences')")
            page.locator('#sentence-options input').first.check()
            page.wait_for_function("document.querySelector('#source-status').textContent.includes('1 selected')")
            assert requests == [], requests
            # Explicit lookup can only read the static manifest and hashed artifacts.
            page.locator('#summary-lookup').click()
            page.wait_for_function("document.querySelector('#summary-status').textContent.startsWith('Not generated for')")
            missing = json.loads(page.locator('#summary-notes').text_content())
            assert missing['generated_text'] is None
            assert missing['model_inference_performed'] is False
            assert missing['confidence'] is None
            # A held lookup cannot overwrite Clear or a later edited source.
            held = []
            page.route('**/source-summary-manifest.json', lambda route: held.append(route))
            page.locator('#summary-lookup').click()
            page.wait_for_timeout(100)
            assert held
            page.locator('#source-clear').click()
            manifest = json.loads((site / 'execution/source-summary-manifest.json').read_text())
            held[0].fulfill(status=200, content_type='application/json', body=json.dumps(manifest))
            page.wait_for_timeout(100)
            assert page.locator('#generated-summary').inner_text() == ''
            assert page.locator('#summary-lookup').is_disabled()
            page.unroute('**/source-summary-manifest.json')
            # Load the explicit public case, not an invented answer for arbitrary text.
            page.locator('#source-examples').locator('xpath=..').locator('summary').click()
            page.locator('#source-examples').click()
            page.wait_for_function("document.querySelector('#source-cases').disabled === false")
            choices = [choice for source in manifest['sources'] for choice in source['choices']]
            assert choices
            page.locator('#source-cases').select_option('0')
            page.wait_for_function("document.querySelector('#summary-lookup').disabled === false")
            for _ in range(2):
                page.locator('#summary-lookup').click()
                page.wait_for_function("!document.querySelector('#summary-lookup').disabled")
                actual = json.loads(page.locator('#summary-notes').text_content())
                if manifest['entries'][choices[0]['source_selection_request_sha256']]['status'] == 'generated':
                    assert actual['status'] == 'generated', actual
                    assert actual['model_inference_performed'] is True
                    assert actual['generated_text'].strip()
                    assert page.locator('#generated-summary').inner_text() == actual['generated_text']
                    assert actual['confidence'] is None
                    assert actual['summary_request']['sampler']['top_k'] == 40
                else:
                    assert not require_generated, 'Docker export was expected to execute the pinned model'
                    assert actual['status'] == 'not_generated'
                    assert actual['generated_text'] is None
            # Read every declared generated receipt through the actual UI. A token
            # cap is a partial output, never a completed-quality claim. No model runs.
            captured_partial = False
            for index, choice in enumerate(choices):
                entry = manifest['entries'][choice['source_selection_request_sha256']]
                if entry['status'] != 'generated':
                    continue
                expected = json.loads((site / 'execution' / entry['receipt']).read_text())['receipt']
                page.locator('#source-cases').select_option('')
                page.locator('#source-cases').select_option(str(index))
                page.wait_for_function("document.querySelector('#summary-lookup').disabled === false")
                page.locator('#summary-lookup').click()
                page.wait_for_function("!document.querySelector('#summary-lookup').disabled")
                actual = json.loads(page.locator('#summary-notes').text_content())
                assert actual['status'] == 'generated'
                assert actual['provenance']['source_selection_request_sha256'] == choice['source_selection_request_sha256']
                assert actual['generated_text'] == expected['generated_text']
                assert page.locator('#generated-summary').inner_text() == expected['generated_text']
                assert actual['truncated'] is expected['truncated']
                assert actual['confidence'] is None
                status = page.locator('#summary-status').inner_text()
                assert status.startswith('Partial output:') is expected['truncated'], status
                assert 'confidence is unknown' in status and 'quality is not guaranteed' in status
                if expected['truncated']:
                    assert 'stopped at the token limit' in status
                    if not captured_partial:
                        screenshot.parent.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(screenshot.with_name(screenshot.stem + '-truncated.png')), full_page=True)
                        captured_partial = True
            assert all(url == base + 'source-summary-manifest.json' or (((url.startswith(base + 'summaries/') and url.endswith('.json')) or (url.startswith(base + 'summary-logs/') and url.endswith('.jsonl'))) and '?' not in url) for url in requests), requests
            page.locator('#source-text').fill('Edited after lookup.')
            assert page.locator('#generated-summary').inner_text() == ''
            assert page.locator('#summary-lookup').is_disabled()
            # Restore an exact public selection for the screenshot.
            page.locator('#source-cases').select_option('')
            page.locator('#source-cases').select_option('1' if len(choices) > 1 else '0')
            page.wait_for_function("document.querySelector('#summary-lookup').disabled === false")
            page.locator('#summary-lookup').click()
            page.wait_for_function("!document.querySelector('#summary-lookup').disabled")
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
    parser.add_argument('--require-generated', action='store_true')
    args = parser.parse_args()
    check(args.site, args.screenshot, args.chromium_path, require_generated=args.require_generated)
