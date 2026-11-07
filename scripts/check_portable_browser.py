"""Exercise exported portable UI on a local static server (Chromium, no backend)."""
from __future__ import annotations
import argparse, hashlib, json, shutil, tempfile, threading
from functools import partial; from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer; from pathlib import Path
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
            if not (logo.is_visible()):
                raise SystemExit('check_portable_browser:35: validation failed')
            page.wait_for_function("document.querySelector('.brand-logo').naturalWidth === 460")
            if not (logo.evaluate('(img) => img.naturalHeight') == 500):
                raise SystemExit('check_portable_browser:37: validation failed')
            favicon = page.locator('link[rel=icon]')
            if not (favicon.get_attribute('type') == 'image/png'):
                raise SystemExit('check_portable_browser:39: validation failed')
            if not (favicon.get_attribute('sizes') == '180x180'):
                raise SystemExit('check_portable_browser:40: validation failed')
            meta = page.request.get(base + 'build.json').json()
            for locator, attribute in [(logo, 'src'), (favicon, 'href')]:
                relative = locator.get_attribute(attribute)
                url = locator.evaluate('(element, attribute) => element[attribute]', attribute)
                if not (url == base + relative):
                    raise SystemExit('check_portable_browser:45: validation failed')
                response = page.request.get(url)
                if not (response.status == 200):
                    raise SystemExit('check_portable_browser:47: validation failed')
                if not (response.headers['content-type'].startswith('image/png')):
                    raise SystemExit('check_portable_browser:48: validation failed')
                if not (hashlib.sha256(response.body()).hexdigest() == meta['assets'][relative]):
                    raise SystemExit('check_portable_browser:49: validation failed')
            if not (page.evaluate('''async () => {
                const icon = new Image();
                icon.src = document.querySelector('link[rel=icon]').href;
                await icon.decode();
                return icon.naturalWidth === 180 && icon.naturalHeight === 180;
            }''')):
                raise SystemExit('check_portable_browser:50: validation failed')
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('Input ready')")
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'ok · deterministic_log_analysis'")
            result = json.loads(page.locator('#result').inner_text())
            if not (result['metrics']['tool_calls'] == 2):
                raise SystemExit('check_portable_browser:60: validation failed')
            if not (result['confidence'] is None):
                raise SystemExit('check_portable_browser:61: validation failed')
            # Repeated runs and edited inputs cannot keep stale evidence visible.
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'ok · deterministic_log_analysis'")
            page.locator('#request').fill('not JSON')
            if not (page.locator('#status').inner_text() == 'Not executed for this input.'):
                raise SystemExit('check_portable_browser:66: validation failed')
            page.locator('#run').click()
            if not (json.loads(page.locator('#result').inner_text())['error']['code'] == 'invalid_input'):
                raise SystemExit('check_portable_browser:68: validation failed')
            page.locator('#adapter').click()
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('Input ready')")
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'ok · build_artifact'")
            value = json.loads(page.locator('#request').input_value())
            value['input']['selector']['query'] = 'never-built'
            page.locator('#request').fill(json.dumps(value))
            page.locator('#run').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'cache_miss · unsupported'")
            if not (json.loads(page.locator('#result').inner_text())['confidence'] is None):
                raise SystemExit('check_portable_browser:78: validation failed')
            # Clear while a fetch is pending cannot be overwritten by its late answer.
            pending = []
            page.route('**/adapter-request.json', lambda route: pending.append(route))
            page.locator('#adapter').click()
            page.wait_for_timeout(100)
            if not (pending):
                raise SystemExit('Expected a held example fetch')
            page.locator('#clear').click()
            pending[0].fulfill(status=200, content_type='application/json', body=json.dumps(value))
            page.wait_for_timeout(100)
            if not (page.locator('#request').input_value() == ''):
                raise SystemExit('check_portable_browser:88: validation failed')
            if not (page.locator('#status').inner_text() == 'Cleared.'):
                raise SystemExit('check_portable_browser:89: validation failed')
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
            if not (initial['model_inference_performed'] is False):
                raise SystemExit('check_portable_browser:103: validation failed')
            if not (initial['confidence'] is None):
                raise SystemExit('check_portable_browser:104: validation failed')
            keyword = next(item['id'] for item in initial['keyword_options'] if item['text'] == 'Graphs')
            page.locator('#keyword-options').select_option(keyword)
            page.wait_for_function("document.querySelector('#source-status').textContent.startsWith('2 candidate sentences')")
            page.locator('#sentence-options input').first.check()
            page.wait_for_function("document.querySelector('#source-status').textContent.includes('1 selected')")
            selected = json.loads(page.locator('#source-notes').text_content())
            if not (selected['summary_request']['generated_text'] is None):
                raise SystemExit('check_portable_browser:111: validation failed')
            if not (page.locator('#summary-download').is_enabled()):
                raise SystemExit('check_portable_browser:112: validation failed')
            if not (page.locator('#selected-evidence').inner_text() == 'Graphs connect nodes.'):
                raise SystemExit('check_portable_browser:113: validation failed')
            if not (page.locator('#sentence-options img').count() == 0):
                raise SystemExit('check_portable_browser:114: validation failed')
            with page.expect_download() as download_info:
                page.locator('#summary-download').click()
            payload = json.loads(Path(download_info.value.path()).read_text())
            if not (payload == selected['summary_request']):
                raise SystemExit('check_portable_browser:118: validation failed')
            with page.expect_download() as case_info:
                page.locator('#source-case-download').click()
            case_payload = json.loads(Path(case_info.value.path()).read_text())
            if not (case_payload['operation'] == 'source_selection'):
                raise SystemExit('check_portable_browser:122: validation failed')
            if not (case_payload['input']['text'] == source):
                raise SystemExit('check_portable_browser:123: validation failed')
            if not (case_payload['input']['sentence_ids'] == [selected['selected_sentences'][0]['id']]):
                raise SystemExit('check_portable_browser:124: validation failed')
            # Edited input, repeated extraction and Clear discard all old evidence.
            page.locator('#source-text').fill('Different input.')
            if not (page.locator('#keyword-options').is_disabled()):
                raise SystemExit('check_portable_browser:127: validation failed')
            if not (page.locator('#summary-download').is_disabled()):
                raise SystemExit('check_portable_browser:128: validation failed')
            if not (page.locator('#source-case-download').is_disabled()):
                raise SystemExit('check_portable_browser:129: validation failed')
            if not (page.locator('#selected-evidence').inner_text() == ''):
                raise SystemExit('check_portable_browser:130: validation failed')
            if not (page.locator('#sentence-options input').count() == 0):
                raise SystemExit('check_portable_browser:131: validation failed')
            page.locator('#source-extract').click()
            page.locator('#source-clear').click()
            page.wait_for_timeout(100)
            if not (page.locator('#source-status').inner_text() == 'Source cleared.'):
                raise SystemExit('check_portable_browser:135: validation failed')
            if not (page.locator('#keyword-options option').count() == 0):
                raise SystemExit('check_portable_browser:136: validation failed')
            page.locator('#source-extract').click()
            page.wait_for_function("document.querySelector('#source-status').textContent.startsWith('No source text')")
            if not (page.locator('#summary-download').is_disabled()):
                raise SystemExit('check_portable_browser:139: validation failed')
            page.locator('#source-text').fill('x' * (128 * 1024 + 1))
            page.locator('#source-extract').click()
            page.wait_for_function("document.querySelector('#source-notes').textContent.includes('invalid_input')")
            if not (page.locator('#summary-download').is_disabled()):
                raise SystemExit('check_portable_browser:143: validation failed')
            page.locator('#source-text').fill('Graphs connect nodes. Graphs support route analysis. Source sentences remain inspectable.')
            for _ in range(2):
                page.locator('#source-extract').click()
                page.wait_for_function("document.querySelector('#source-status').textContent.includes('candidate sentences')")
            page.locator('#sentence-options input').first.check()
            page.wait_for_function("document.querySelector('#source-status').textContent.includes('1 selected')")
            if not (requests == []):
                raise SystemExit(requests)
            # Explicit lookup can only read the static manifest and hashed artifacts.
            page.locator('#summary-lookup').click()
            page.wait_for_function("document.querySelector('#summary-status').textContent.startsWith('Not generated for')")
            missing = json.loads(page.locator('#summary-notes').text_content())
            if not (missing['generated_text'] is None):
                raise SystemExit('check_portable_browser:155: validation failed')
            if not (missing['model_inference_performed'] is False):
                raise SystemExit('check_portable_browser:156: validation failed')
            if not (missing['confidence'] is None):
                raise SystemExit('check_portable_browser:157: validation failed')
            # A held lookup cannot overwrite Clear or a later edited source.
            held = []
            page.route('**/source-summary-manifest.json', lambda route: held.append(route))
            page.locator('#summary-lookup').click()
            page.wait_for_timeout(100)
            if not (held):
                raise SystemExit('check_portable_browser:163: validation failed')
            page.locator('#source-clear').click()
            manifest = json.loads((site / 'execution/source-summary-manifest.json').read_text())
            held[0].fulfill(status=200, content_type='application/json', body=json.dumps(manifest))
            page.wait_for_timeout(100)
            if not (page.locator('#generated-summary').inner_text() == ''):
                raise SystemExit('check_portable_browser:168: validation failed')
            if not (page.locator('#summary-lookup').is_disabled()):
                raise SystemExit('check_portable_browser:169: validation failed')
            page.unroute('**/source-summary-manifest.json')
            # Load the explicit public case, not an invented answer for arbitrary text.
            page.locator('#source-examples').locator('xpath=..').locator('summary').click()
            page.locator('#source-examples').click()
            page.wait_for_function("document.querySelector('#source-cases').disabled === false")
            choices = [choice for source in manifest['sources'] for choice in source['choices']]
            if not (choices):
                raise SystemExit('check_portable_browser:176: validation failed')
            page.locator('#source-cases').select_option('0')
            page.wait_for_function("document.querySelector('#summary-lookup').disabled === false")
            for _ in range(2):
                page.locator('#summary-lookup').click()
                page.wait_for_function("!document.querySelector('#summary-lookup').disabled")
                actual = json.loads(page.locator('#summary-notes').text_content())
                if manifest['entries'][choices[0]['source_selection_request_sha256']]['status'] == 'generated':
                    if not (actual['status'] == 'generated'):
                        raise SystemExit(actual)
                    if not (actual['model_inference_performed'] is True):
                        raise SystemExit('check_portable_browser:185: validation failed')
                    if not (actual['generated_text'].strip()):
                        raise SystemExit('check_portable_browser:186: validation failed')
                    if not (page.locator('#generated-summary').inner_text() == actual['generated_text']):
                        raise SystemExit('check_portable_browser:187: validation failed')
                    if not (actual['confidence'] is None):
                        raise SystemExit('check_portable_browser:188: validation failed')
                    if not (actual['summary_request']['sampler']['top_k'] == 40):
                        raise SystemExit('check_portable_browser:189: validation failed')
                else:
                    if not (not require_generated):
                        raise SystemExit('Docker export was expected to execute the pinned model')
                    if not (actual['status'] == 'not_generated'):
                        raise SystemExit('check_portable_browser:192: validation failed')
                    if not (actual['generated_text'] is None):
                        raise SystemExit('check_portable_browser:193: validation failed')
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
                if not (actual['status'] == 'generated'):
                    raise SystemExit('check_portable_browser:208: validation failed')
                if not (actual['provenance']['source_selection_request_sha256'] == choice['source_selection_request_sha256']):
                    raise SystemExit('check_portable_browser:209: validation failed')
                if not (actual['generated_text'] == expected['generated_text']):
                    raise SystemExit('check_portable_browser:210: validation failed')
                if not (page.locator('#generated-summary').inner_text() == expected['generated_text']):
                    raise SystemExit('check_portable_browser:211: validation failed')
                if not (actual['truncated'] is expected['truncated']):
                    raise SystemExit('check_portable_browser:212: validation failed')
                if not (actual['confidence'] is None):
                    raise SystemExit('check_portable_browser:213: validation failed')
                status = page.locator('#summary-status').inner_text()
                if not (status.startswith('Partial output:') is expected['truncated']):
                    raise SystemExit(status)
                if not ('confidence is unknown' in status and 'quality is not guaranteed' in status):
                    raise SystemExit('check_portable_browser:216: validation failed')
                if expected['truncated']:
                    if not ('stopped at the token limit' in status):
                        raise SystemExit('check_portable_browser:218: validation failed')
                    if not captured_partial:
                        screenshot.parent.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(screenshot.with_name(screenshot.stem + '-truncated.png')), full_page=True)
                        captured_partial = True
            if not (all(url == base + 'source-summary-manifest.json' or (((url.startswith(base + 'summaries/') and url.endswith('.json')) or (url.startswith(base + 'summary-logs/') and url.endswith('.jsonl'))) and '?' not in url) for url in requests)):
                raise SystemExit(requests)
            page.locator('#source-text').fill('Edited after lookup.')
            if not (page.locator('#generated-summary').inner_text() == ''):
                raise SystemExit('check_portable_browser:225: validation failed')
            if not (page.locator('#summary-lookup').is_disabled()):
                raise SystemExit('check_portable_browser:226: validation failed')
            # Restore an exact public selection for the screenshot.
            page.locator('#source-cases').select_option('')
            page.locator('#source-cases').select_option('1' if len(choices) > 1 else '0')
            page.wait_for_function("document.querySelector('#summary-lookup').disabled === false")
            page.locator('#summary-lookup').click()
            page.wait_for_function("!document.querySelector('#summary-lookup').disabled")
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            if not (page.evaluate('document.documentElement.scrollWidth <= innerWidth')):
                raise SystemExit('check_portable_browser:236: validation failed')
            if not (logo.is_visible()):
                raise SystemExit('check_portable_browser:237: validation failed')
            if not (logo.evaluate('(img) => img.naturalWidth') == 460):
                raise SystemExit('check_portable_browser:238: validation failed')
            page.screenshot(path=str(screenshot.with_name(screenshot.stem + '-mobile.png')), full_page=True)
            if not (not errors):
                raise SystemExit(errors)
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
