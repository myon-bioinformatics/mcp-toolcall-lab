import importlib.util
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_redirect_preserves_routes_and_public_export_is_still_retained():
    script = ROOT / 'scripts/write_portfolio_redirect.py'
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(['python', '-S', str(script), directory], check=True)
        html = (Path(directory) / 'index.html').read_text()
        assert 'https://myon-bioinformatics.github.io/tools/mcp-toolcall-lab/' in html
        js = html.split('<script>')[1].split('</script>')[0]
        for route in ('#wiki', '#pixiv', ''):
            node = "let result; const location={search:'?view=wiki',hash:" + repr(route) + ",replace:v=>{result=v}};" + js + ";process.stdout.write(result)"
            result = subprocess.run(['node', '-e', node], text=True, capture_output=True, check=True)
            assert result.stdout.endswith('?view=wiki' + route)
    workflow = (ROOT / '.github/workflows/stub-pages.yml').read_text()
    assert 'path: _portfolio_redirect' in workflow
    assert 'stub-pages-observations' in workflow
    assert '            _site/' in workflow
    assert 'scripts/stub_pages_smoke.py' in workflow
