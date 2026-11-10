"""The browser check must reject invalid observations under normal Python and -O."""
import subprocess, sys, unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_portable_browser.py'


class PortableCheckOptimizationTests(unittest.TestCase):
    def test_browser_observations_and_cleanup(self):
        for optimized in (False, True):
            for change in ('none', 'logo', 'height', 'favicon', 'status', 'digest', 'decode'):
                with self.subTest(optimized=optimized, change=change):
                    code = r'''
import hashlib, runpy, sys, tempfile, types
from pathlib import Path
from unittest.mock import MagicMock, patch
script, change = sys.argv[1:]
module = types.ModuleType('playwright.sync_api')
module.sync_playwright = MagicMock()
with patch.dict(sys.modules, {'playwright': types.ModuleType('playwright'), 'playwright.sync_api': module}):
    g = runpy.run_path(script)['check'].__globals__
page, logo, favicon = MagicMock(), MagicMock(), MagicMock()
server = MagicMock(server_port=12345)
base = 'http://127.0.0.1:12345/mcp-toolcall-lab/execution/'
logo.is_visible.return_value = change != 'logo'
logo.evaluate.side_effect = lambda js, *args: base + 'logo.png' if args else (499 if change == 'height' else 500)
logo.get_attribute.return_value = 'logo.png'
favicon.get_attribute.side_effect = lambda key: {'type': 'text/plain' if change == 'favicon' else 'image/png', 'sizes': '180x180', 'href': 'icon.png'}[key]
favicon.evaluate.return_value = base + 'icon.png'
page.locator.side_effect = lambda selector: logo if selector == '.brand-logo' else favicon if selector == 'link[rel=icon]' else MagicMock()
response = MagicMock(status=404 if change == 'status' else 200, headers={'content-type': 'image/png'})
response.body.return_value = b'wrong' if change == 'digest' else b'image'
response.json.return_value = {'assets': {name: hashlib.sha256(b'image').hexdigest() for name in ('logo.png', 'icon.png')}}
page.request.get.return_value = response
page.evaluate.return_value = change != 'decode'
class ProbeComplete(Exception): pass
def wait(js):
    if 'Input ready' in js: raise ProbeComplete()
page.wait_for_function.side_effect = wait
playwright = module.sync_playwright.return_value.__enter__.return_value
playwright.chromium.launch.return_value.new_page.return_value = page
with tempfile.TemporaryDirectory() as tmp:
    site = Path(tmp) / 'site'
    site.mkdir()
    screenshot = Path(tmp) / 'evidence.png'
    try:
        with patch.dict(g, {'ThreadingHTTPServer': MagicMock(return_value=server)}), patch.object(g['threading'], 'Thread'):
            g['check'](site, screenshot)
    except ProbeComplete:
        if change != 'none': raise RuntimeError('invalid observation accepted')
        if not page.evaluate.called: raise RuntimeError('browser decode was skipped')
        print('valid observations accepted')
    finally:
        if server.shutdown.call_count != 1 or server.server_close.call_count != 1:
            raise RuntimeError('server cleanup skipped')
        if screenshot.exists(): raise RuntimeError('unexpected success screenshot')
'''
                    result = subprocess.run([sys.executable, '-S', *(['-O'] if optimized else []), '-c', code,
                                             str(SCRIPT), change], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0 if change == 'none' else 1, result.stderr)
                    self.assertEqual(result.stdout, 'valid observations accepted\n' if change == 'none' else '')
                    if change != 'none':
                        self.assertIn('validation failed', result.stderr)
                        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
