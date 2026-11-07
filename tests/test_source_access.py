"""All transport is injected; saved-source reprocessing must never access network."""
import hashlib, io, json, socket
from email.message import Message; from pathlib import Path; from unittest.mock import Mock; from urllib.error import HTTPError

import pytest

from mcp_toolcall_lab import source_access as source
from mcp_toolcall_lab.adapters.html_snapshot import extract, MAX_BYTES


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('unexpected network access')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, content_type='text/html; charset=utf-8', encoding=None):
        super().__init__(body)
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        if encoding:
            self.headers['Content-Encoding'] = encoding


def test_one_get_many_offline_extractions():
    html = '<main><article class="markdown-body"><h1>Calendar</h1>' + ''.join(
        f'<a href="/day/{i}">{i}日</a>' for i in range(1, 32)) + '</article></main>'
    opener = Mock(return_value=Response(html.encode()))
    snapshot = source.fetch_snapshot('https://example.test/month', opener=opener)
    registry = source.default_registry()
    for profile in ['generic', 'github-readme', 'generic']:
        result = registry.invoke('html', 'extract', {'snapshot': snapshot, 'profile': profile})
        data = result['data']['extraction']
        assert len(data['links']) == 31
        assert data['headings'] == [{'level': 'h1', 'text': 'Calendar'}]
        assert data['links'][-1]['url'] == 'https://example.test/day/31'
    assert opener.call_count == 1
    request = opener.call_args.args[0]
    assert request.get_method() == 'GET'
    assert request.data is None
    assert opener.call_args.kwargs == {'timeout': 15}
    assert snapshot['response_sha256'] == snapshot['content_sha256']


def test_hidden_subtrees_and_multiple_articles():
    html = '<template><article>fake</article></template><article><h2>A</h2><script>bad()</script>'
    html += '<article>B</article></article><article>C</article><style>bad-css</style>'
    result = extract(html, 'https://example.test/')
    assert result['text'] == 'A B C'
    assert result['headings'] == [{'level': 'h2', 'text': 'A'}]


@pytest.mark.parametrize('html', ['<main>changed</main>', '<article>missing class</article>',
    '<article class="markdown-body">a</article><article class="markdown-body">b</article>'],
    ids=['missing-article', 'missing-class', 'ambiguous-articles'])
def test_github_structure_drift_is_explicit(html):
    with pytest.raises(ValueError, match='exactly one'):
        extract(html, 'https://github.com/owner/repo', 'github-readme')


@pytest.mark.parametrize('html', ['', '<article><script>x</script></article>', '<div>' * 128],
    ids=['empty', 'script-only', 'too-deep'])
def test_empty_or_deep_document_is_not_success(html):
    with pytest.raises(ValueError):
        extract(html, 'https://example.test/')


def test_snapshot_tamper_and_wrong_schema():
    snapshot = source.make_snapshot('<article>original</article>', 'https://example.test/')
    snapshot['html'] = '<article>changed</article>'
    with pytest.raises(ValueError, match='hash mismatch'):
        source.extract_snapshot(snapshot)
    with pytest.raises(ValueError, match='html-snapshot/1'):
        source.extract_snapshot({'schema': 'unknown'})


@pytest.mark.parametrize('body,content_type,encoding', [
    (b'x', 'application/pdf', None), (b'\xff', 'text/html; charset=utf-8', None),
    (b'x' * (MAX_BYTES + 1), 'text/html', None), (b'x', 'text/html', 'gzip')],
    ids=['pdf', 'invalid-utf8', 'oversize', 'gzip'])
def test_bad_response_rejected(body, content_type, encoding):
    with pytest.raises(ValueError):
        source.fetch_snapshot('https://example.test/', opener=lambda *a, **k: Response(body, content_type, encoding))


def test_declared_charset_and_no_redirect():
    text = '<article>日本語</article>'
    snap = source.fetch_snapshot('https://example.test/', opener=lambda *a, **k:
                                 Response(text.encode('shift_jis'), 'text/html; charset=shift_jis'))
    assert snap['html'] == text
    assert snap['response_sha256'] != snap['content_sha256']
    assert source._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.test') is None


@pytest.mark.parametrize('code', [401, 403, 429, 503])
def test_http_failure_no_retry(code):
    opener = Mock(side_effect=HTTPError('https://example.test', code, 'error', {}, None))
    with pytest.raises(HTTPError) as caught:
        source.fetch_snapshot('https://example.test', opener=opener)
    assert caught.value.code == code
    assert opener.call_count == 1


def test_registry_selection_argument_validation_and_permission():
    r = source.Registry()
    first, second = Mock(), Mock()
    r.register('a', 'read', first, network=True)
    r.register('b', 'read', second)
    with pytest.raises(ValueError, match='allow_network'):
        r.invoke('a', 'read', {})
    r.invoke('b', 'read', {})
    first.assert_not_called()
    second.assert_called_once()
    with pytest.raises(ValueError, match='unknown'):
        r.invoke('unknown', 'read', {})
    with pytest.raises(ValueError, match='duplicate'):
        r.register('a', 'read', first)
    with pytest.raises(ValueError, match='invalid operation arguments'):
        source.default_registry().invoke('html', 'extract', {'wrong': 'argument'})


def test_wikipedia_wrapper_reuses_existing_cache(monkeypatch):
    from mcp_toolcall_lab import wikipedia_tool as wiki
    payload = {'query': {'pages': {'1': {'pageid': 1, 'title': 'Example', 'extract': 'Public text'}}}}
    opener = Mock(side_effect=lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.delenv(wiki.FIXTURE_ENV, raising=False)
    monkeypatch.setattr(wiki.urllib.request, 'urlopen', opener)
    registry = source.default_registry()
    args = {'title': 'Example', 'lang': 'en'}
    with pytest.raises(ValueError, match='allow_network'):
        registry.invoke('wikipedia', 'article', args)
    a = registry.invoke('wikipedia', 'article', args, allow_network=True)
    b = registry.invoke('wikipedia', 'article', args, allow_network=True)
    assert a == b
    assert a['data']['extract'] == 'Public text'
    assert opener.call_count == 1
    with pytest.raises(ValueError):
        registry.invoke('wikipedia', 'article', {'title': 'X', 'lang': '../bad'}, allow_network=True)
    assert opener.call_count == 1


def test_cli_offline_and_native_error(tmp_path, capsys):
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'provider': 'html', 'operation': 'extract', 'arguments': {
        'snapshot': source.make_snapshot('<main>saved</main>', 'https://example.test/')
    }}))
    assert source.main([str(request)]) == 0
    assert json.loads(capsys.readouterr().out)['result']['data']['extraction']['text'] == 'saved'
    request.write_text('{')
    assert source.main([str(request)]) == 2
    assert json.loads(capsys.readouterr().out)['ok'] is False


def test_cli_existing_snapshot_never_fetches(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'existing.json'
    path.write_text('keep')
    fetcher = Mock(side_effect=AssertionError('must not fetch'))
    monkeypatch.setattr(source, 'fetch_snapshot', fetcher)
    assert source.main([str(path), '--fetch-url', 'https://example.test', '--allow-network']) == 2
    assert path.read_text() == 'keep'
    fetcher.assert_not_called()
    assert 'already exists' in capsys.readouterr().out


def test_cli_dangling_symlink_never_fetches(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'dangling.json'
    path.symlink_to(tmp_path / 'missing-target.json')
    assert path.is_symlink() and not path.exists()
    fetcher = Mock(side_effect=AssertionError('must not fetch'))
    monkeypatch.setattr(source, 'fetch_snapshot', fetcher)
    assert source.main([str(path), '--fetch-url', 'https://example.test', '--allow-network']) == 2
    assert path.is_symlink()
    assert path.readlink() == tmp_path / 'missing-target.json'
    fetcher.assert_not_called()
    assert 'already exists' in capsys.readouterr().out


def test_cli_capture_and_local_reuse(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'new.json'
    snap = source.make_snapshot('<main>cached</main>', 'https://example.test')
    fetcher = Mock(return_value=snap)
    monkeypatch.setattr(source, 'fetch_snapshot', fetcher)
    assert source.main([str(path), '--fetch-url', 'https://example.test', '--allow-network']) == 0
    assert 'html' not in json.loads(capsys.readouterr().out)['result']
    assert source.extract_snapshot(json.loads(path.read_text()))['extraction']['text'] == 'cached'
    fetcher.assert_called_once()


# --- PokéAPI: the API path and the "view source" path give one result -------------------------------------------

DITTO = (Path(__file__).resolve().parents[1] / 'fixtures' / 'pokeapi' / 'ditto.json').read_text(encoding='utf-8')


@pytest.fixture
def poke(monkeypatch):
    """Registry whose single-GET transport is a Mock answering with the fixture; the process cache starts empty."""
    from mcp_toolcall_lab.adapters import pokeapi
    pokeapi._CACHE.clear()
    opener = Mock(side_effect=lambda *a, **k: Response(DITTO.encode(), 'application/json; charset=utf-8'))
    original = source.fetch_bytes
    monkeypatch.setattr(source, 'fetch_bytes', lambda *a, **k: original(*a, **k, opener=opener))
    return source.default_registry(), opener


def test_pokeapi_source_is_offline_and_text_or_object_read_the_same():
    registry = source.default_registry()
    text = registry.invoke('pokeapi', 'source', {'source': DITTO})['data']
    obj = registry.invoke('pokeapi', 'source', {'source': json.loads(DITTO), 'url': 'https://pokeapi.co/api/v2/pokemon/ditto'})['data']
    assert {k: v for k, v in text.items() if k != 'source'} == {k: v for k, v in obj.items() if k != 'source'}
    assert (text['id'], text['name'], text['types'], text['height_m'], text['weight_kg']) == (132, 'ditto', ['normal'], 0.3, 4.0)
    assert text['abilities'] == [{'name': 'limber', 'hidden': False}, {'name': 'imposter', 'hidden': True}]
    assert text['stats'] == {'hp': 48, 'attack': 48, 'defense': 48, 'special-attack': 48, 'special-defense': 48, 'speed': 48}
    assert text['markdown'].startswith('# ditto (#132)\n\n| type | height m | weight kg |')
    assert '| imposter | yes |' in text['markdown'] and '| hp | 48 |' in text['markdown']
    assert text['source']['sha256'] == hashlib.sha256(DITTO.encode()).hexdigest() and text['source']['url'] is None
    assert obj['source']['url'] == 'https://pokeapi.co/api/v2/pokemon/ditto'


def test_pokeapi_api_path_is_one_get_then_the_cache_and_equals_the_source_path(poke):
    registry, opener = poke
    with pytest.raises(ValueError, match='allow_network'):
        registry.invoke('pokeapi', 'pokemon', {'name': 'ditto'})
    opener.assert_not_called()
    first = registry.invoke('pokeapi', 'pokemon', {'name': ' Ditto '}, allow_network=True)['data']
    again = registry.invoke('pokeapi', 'pokemon', {'name': 'ditto'}, allow_network=True)['data']
    assert opener.call_count == 1 and first == again
    request = opener.call_args.args[0]
    assert request.full_url == 'https://pokeapi.co/api/v2/pokemon/ditto' and request.get_method() == 'GET' and request.data is None
    assert request.get_header('Accept') == 'application/json' and opener.call_args.kwargs == {'timeout': 15}
    offline = registry.invoke('pokeapi', 'source', {'source': DITTO, 'url': first['source']['url']})['data']
    assert offline == first                                                                                      # same text, same hash, same record
    again['name'] = 'changed'
    assert registry.invoke('pokeapi', 'pokemon', {'name': 'ditto'}, allow_network=True)['data']['name'] == 'ditto'   # callers cannot corrupt the cache


def test_pokeapi_names_are_checked_before_any_request_and_the_cache_is_bounded(poke):
    registry, opener = poke
    for bad in ['', ' ', '../etc', 'a b', 'ditto?x=1', 'a' * 41, '-x', True, 1.5, None, ['ditto']]:
        with pytest.raises(ValueError):
            registry.invoke('pokeapi', 'pokemon', {'name': bad}, allow_network=True)
    with pytest.raises(ValueError, match='invalid operation arguments'):
        registry.invoke('pokeapi', 'pokemon', {'name': 'ditto', 'opener': 'x'}, allow_network=True)             # the transport is not a request argument
    opener.assert_not_called()
    from mcp_toolcall_lab.adapters import pokeapi
    for number in range(1, pokeapi.CACHE_MAXSIZE + 3):
        registry.invoke('pokeapi', 'pokemon', {'name': str(number)}, allow_network=True)
    assert len(pokeapi._CACHE) == pokeapi.CACHE_MAXSIZE and '1' not in pokeapi._CACHE and str(pokeapi.CACHE_MAXSIZE + 2) in pokeapi._CACHE


@pytest.mark.parametrize('body,content_type,encoding', [
    (DITTO.encode(), 'text/html', None), (DITTO.encode(), 'application/json', 'gzip'), (b'{' + b' ' * (2 * 1024 * 1024), 'application/json', None),
    (b'<html>', 'application/json', None), (b'[1]', 'application/json', None), (b'{"id":1}', 'application/json', None)],
    ids=['html', 'gzip', 'oversize', 'not-json', 'not-an-object', 'wrong-shape'])
def test_pokeapi_bad_responses_are_errors_not_records(monkeypatch, body, content_type, encoding):
    from mcp_toolcall_lab.adapters import pokeapi
    pokeapi._CACHE.clear()
    original = source.fetch_bytes
    monkeypatch.setattr(source, 'fetch_bytes', lambda *a, **k: original(*a, **k, opener=lambda *x, **y: Response(body, content_type, encoding)))
    with pytest.raises(ValueError):
        source.default_registry().invoke('pokeapi', 'pokemon', {'name': 'ditto'}, allow_network=True)
    assert not pokeapi._CACHE


@pytest.mark.parametrize('given', ['', 'nope', '[]', '{}', '{"id":"x","name":"a","height":1,"weight":1,"stats":[],"types":[],"abilities":[]}', 5, None,
                                   json.dumps({**json.loads(DITTO), 'name': '../x'}), json.dumps({**json.loads(DITTO), 'stats': []})])
def test_pokeapi_source_refuses_what_is_not_a_pokemon(given):
    with pytest.raises(ValueError):
        source.default_registry().invoke('pokeapi', 'source', {'source': given})
    with pytest.raises(ValueError):
        source.default_registry().invoke('pokeapi', 'source', {'source': DITTO, 'url': 'https://evil.test/api/v2/pokemon/ditto'})


def test_pokeapi_cli_reads_pasted_json_with_no_network(tmp_path, capsys):
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'provider': 'pokeapi', 'operation': 'source', 'arguments': {'source': json.loads(DITTO)}}), encoding='utf-8')
    assert source.main([str(request)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['ok'] and out['result']['data']['name'] == 'ditto' and out['result']['data']['types'] == ['normal']
    assert source.main([str(request), '--allow-network']) == 0
