"""The screenshot job keeps its real browser while omitting unrelated servers."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_screenshot_group_is_optional_and_reuses_locked_browser():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding="utf-8"))
    group = project['dependency-groups']['ci-wiki-screenshot']
    assert group == project['project']['optional-dependencies']['browser-test']
    assert group == ['playwright>=1.64.1']
    defaults = project['tool']['uv'].get('default-groups', ['dev'])
    assert isinstance(defaults, list)
    assert all(isinstance(group, str) for group in defaults)
    assert 'ci-wiki-screenshot' not in defaults
    assert project['project']['dependencies'] == [
        'fastmcp>=4.1.3', 'mcp>=2.3.0', 'pydantic>=2.14.1',
    ]
    assert 'test' in project['project']['optional-dependencies']
    lock = tomllib.loads((ROOT / 'uv.lock').read_text(encoding="utf-8"))
    package = next(p for p in lock['package'] if p['name'] == 'mcp-toolcall-lab')
    assert package['dev-dependencies']['ci-wiki-screenshot'] == [{'name': 'playwright'}]


def test_all_screenshot_commands_use_the_same_exact_group():
    workflow = (ROOT / '.github/workflows/wikipedia-article-screenshot.yml').read_text(encoding="utf-8")
    prefix = 'uv run --locked --only-group ci-wiki-screenshot --exact '
    commands = [line.strip().removeprefix('run: ') for line in workflow.splitlines()
                if line.strip().startswith('run: ')]
    assert commands == [
        prefix + 'playwright install --with-deps --only-shell chromium',
        prefix + 'python scripts/wikipedia_article_screenshot.py --out test-results/wiki-screenshots',
        prefix + 'python scripts/wikipedia_article_screenshot.py --live --out test-results/wiki-screenshots',
    ]
    assert "github.event_name != 'workflow_dispatch' || inputs.live_wikipedia != true" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.live_wikipedia == true" in workflow
    assert 'MCP_TOOLCALL_LAB_WIKIPEDIA_FIXTURE: ${{ github.workspace }}/fixtures/wikipedia/exampleville_extract.json' in workflow
    assert 'MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA: "1"' in workflow
    assert 'if: always()' in workflow
    assert 'if-no-files-found: error' in workflow
    for path in ['pyproject.toml', 'uv.lock', 'tests/test_ci_wiki_screenshot_profile.py']:
        assert f'      - "{path}"' in workflow
    # The script supplies the same source tree without installing the server package.
    script = (ROOT / 'scripts/wikipedia_article_screenshot.py').read_text(encoding="utf-8")
    assert 'sys.path.insert(0, str(ROOT / "src"))' in script
    assert '"--full-page"' in script
    assert 'wiki-title-entered.png' in script and 'wiki-heading-selected.png' in script
