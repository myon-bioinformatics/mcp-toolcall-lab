import socket
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from mcp_toolcall_lab.adapters.css_inspect import inspect_css, declarations
from mcp_toolcall_lab.source_access import default_registry, make_snapshot


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    blocked = Mock(side_effect=AssertionError('CSS inspection must not fetch'))
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


def test_ids_colors_variables_comments_and_duplicate_declarations():
    result = inspect_css('''/* #fff is a comment */
    #calendar, .day { --accent: #abc; color: var(--accent, #112233);
      color: rgb(1, 2, 3) !IMPORTANT; background: url("image.svg#abcdef");
      content: "#fff;}:"; broken; }''')
    rule = result['rules'][0]
    assert rule['selectors'] == ['#calendar', '.day']
    ds = rule['declarations']
    assert ds[0]['hex_colors'] == ['#abc']
    assert ds[1]['variables'] == ['--accent']
    assert ds[1]['hex_colors'] == ['#112233']
    assert ds[2]['value'] == 'rgb(1, 2, 3)' and ds[2]['important']
    assert ds[3]['hex_colors'] == [] and ds[4]['hex_colors'] == []
    assert rule['unknown'] == ['broken']


def test_conditions_import_and_unsupported_blocks_are_not_silently_applied():
    result = inspect_css('''@import url("saved.css");
    @media (max-width: 600px) { @supports (display: grid) { .day {display: grid;} } }
    @font-face {font-family: example; src: url(font.woff);}
    @keyframes spin {from {opacity: 0;} to {opacity: 1;}}''')
    assert result['rules'][0]['contexts'] == [
        '@media (max-width: 600px)', '@supports (display: grid)']
    assert result['at_rules'][0]['header'] == '@import url("saved.css")'
    assert len(result['unknown']) == 2
    assert result['computed_styles'] is False


@pytest.mark.parametrize('css', ['/* unclosed', 'a {color: "x;}', 'a {color: rgb(1;}',
                                 'a { color:red;', '}', 'a {x: ' + '(' * 129],
                         ids=['comment', 'string', 'function', 'block', 'extra-close', 'depth'])
def test_malformed_css_rejected(css):
    with pytest.raises(ValueError):
        inspect_css(css)


def test_inline_embedded_saved_stylesheet_and_unloaded_references():
    html = '''<style>#calendar {color:#000;} .day {color:var(--accent);}
    .day:hover {color:#fff;} .absent {display:none;}</style>
    <link rel="StyleSheet" href="/saved.css"><link rel="stylesheet" href="/unloaded.css">
    <main id="calendar" style="background:#fff; color:rgb(1,2,3)">
      <div class="day">1日</div><div class="day">2日</div></main>'''
    snapshot = make_snapshot(html, 'https://example.test/month')
    registry = default_registry()
    args = {'snapshot': snapshot, 'selector': '#calendar', 'include_css': True,
            'stylesheets': {'/saved.css': ':root {--accent: #1234;} .day {font-weight: bold;}'}}
    for _ in range(2):
        css = registry.invoke('html', 'extract', args)['data']['extraction']['css']
        assert css['unloaded_stylesheets'] == ['https://example.test/unloaded.css']
        assert css['inline'][0]['declarations'][0]['hex_colors'] == ['#fff']
        matches = css['selector_matches']
        assert matches[0]['matched'] == 1 and matches[1]['matched'] == 2
        assert matches[2]['status'] == 'unsupported' and matches[2]['matched'] is None
        assert matches[3]['status'] == 'unmatched'
        assert matches[-1]['matched'] == 2
    assert registry.invoke('css', 'inspect', {'css': 'body {color:#fff;}'})['data']['rules']


def test_balanced_tokens_in_data_urls_strings_and_custom_properties():
    result = declarations('background:url("data:image/svg+xml;a:b,{x}"); --x:var(--y, rgb(1,2,3)); content:"a:b;c";')
    assert len(result['declarations']) == 3
    assert not result['unknown']
    assert result['declarations'][1]['variables'] == ['--y']


def test_pinned_web_ui_reference_styles_are_read_without_fetching():
    fixture = json.loads((Path(__file__).parent / 'fixtures/web-ui-css-reference.json').read_text())
    assert fixture['source_commit'] == '365b33dc2c16cf1f90b91e88095cbb576f49cbd9'
    html = '<html><head></head><body><main class="ui-page"><section class="ui-panel">Hello</section></main></body></html>'
    result = default_registry().invoke('html', 'extract', {
        'snapshot': make_snapshot(html, 'https://example.test'), 'include_css': True,
        'stylesheets': fixture['sources']})['data']['extraction']['css']
    token_rule = result['sources'][0]['inspection']['rules'][0]
    assert token_rule['selectors'] == [':root']
    assert token_rule['declarations'][0]['property'] == '--ui-bg'
    assert token_rule['declarations'][0]['hex_colors'] == ['#0d1117']
    assert result['selector_matches'][0]['matched'] == 1
    assert result['sources'][1]['inspection']['rules'][0]['contexts'] == ['@layer base']
    assert any(match['selector'] == '.ui-panel' and match['matched'] == 1
               for match in result['selector_matches'])


def test_inactive_style_subtrees_are_not_treated_as_active_sources():
    from mcp_toolcall_lab.adapters.css_inspect import inspect_html_styles
    result = inspect_html_styles('<template><style>body{color:#fff}</style></template><main>x</main>',
                                 'https://example.test')
    assert result['sources'] == []
