from unittest.mock import Mock

import pytest

from mcp_toolcall_lab.adapters.html_snapshot import extract
from mcp_toolcall_lab.adapters.niconico_source import register_niconico
from mcp_toolcall_lab.source_access import default_registry, make_snapshot


HTML = '''<main id="calendar"><section class="month rows">
<div class="day"><h2>1日</h2><a href="/one">A</a></div>
<div class="day"><h2>2日</h2><a href="/two">B</a></div>
<script><div class="day">fake</div></script></section></main>
<aside><div class="day">outside</div></aside>'''


@pytest.mark.parametrize('selector', ['#calendar .day', 'main#calendar > section.month > div.day',
                                     '#calendar>.rows>.day'])
def test_select_all_rows_in_loaded_document(selector):
    result = extract(HTML, 'https://example.test/month', selector=selector)
    assert result['scope_count'] == 2
    assert result['text'] == '1日 A 2日 B'
    assert [link['url'] for link in result['links']] == [
        'https://example.test/one', 'https://example.test/two']


@pytest.mark.parametrize('selector', ['', '#missing', '[id=calendar]', 'div:hover',
                                     'div,aside', '> main', 'main >', 'main >> div'])
def test_invalid_or_missing_selection_fails(selector):
    with pytest.raises(ValueError):
        extract(HTML, 'https://example.test', selector=selector)


def test_nested_matches_are_not_double_counted():
    result = extract('<div class="x">A<div class="x">B</div></div>',
                     'https://example.test', selector='.x')
    assert result['text'] == 'A B'
    assert result['scope_count'] == 1


def test_same_snapshot_different_selectors_without_transport(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', Mock(side_effect=AssertionError('network')))
    registry = default_registry()
    snapshot = make_snapshot(HTML, 'https://example.test/month')
    for selector, text in [('#calendar h2', '1日 2日'), ('#calendar a', 'A B')]:
        result = registry.invoke('html', 'extract', {'snapshot': snapshot, 'selector': selector})
        assert result['data']['extraction']['text'] == text


def test_niconico_binding_preserves_evidence_and_requires_opt_in():
    evidence = {'records': [{'identifier': 'sm1'}], 'complete': False,
                'snapshot_consistent': False, 'page': {'status': 'page_limit'}}
    adapter = Mock()
    adapter.paged_search.return_value = evidence
    registry = default_registry(niconico_adapter=adapter)
    arguments = {'q': '', 'sort': '-startTime', 'context': 'offline',
                 'user_agent': 'offline', 'max_pages': 1}
    with pytest.raises(ValueError, match='allow_network'):
        registry.invoke('niconico', 'search', arguments)
    adapter.paged_search.assert_not_called()
    assert registry.invoke('niconico', 'search', arguments, allow_network=True)['data'] is evidence
    adapter.paged_search.assert_called_once_with(**arguments, targets=None, limit=100,
                                               offset=0, filters=None)
    with pytest.raises(ValueError, match='invalid operation arguments'):
        registry.invoke('niconico', 'search', {**arguments, 'opener': 'code'}, allow_network=True)
