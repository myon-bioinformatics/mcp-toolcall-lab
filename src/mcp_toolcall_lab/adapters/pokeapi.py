"""PokéAPI ``pokemon``: one reader for the API response and for the raw JSON a browser shows.

Two ways in, one result. ``pokemon`` makes one GET (needs ``allow_network``); ``pokemon_source`` reads text
the person already has -- the page opened with the browser's "view source" (or the raw JSON tab) on
``https://pokeapi.co/api/v2/pokemon/<name>`` -- and needs no network at all. Both go through ``parse`` and
return the same record, with a Markdown table from the vendored ``markdown.py`` (the same one-parser rule as
``wikipedia_tool``: a tidy table, not a second converter). Only the fields read below matter; a real
response also carries moves, sprites and game indices, which are ignored. PokéAPI asks clients to cache, so
``pokemon`` keeps a small bounded LRU and the ``source`` path lets one capture be reused offline.
"""
import copy, hashlib, json, re, threading
from collections import OrderedDict

from ..markdown_lib import load_markdown

API = 'https://pokeapi.co/api/v2/pokemon/{name}'
LIMIT = 2 * 1024 * 1024
CACHE_MAXSIZE = 16
_NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,39}')
_CACHE = OrderedDict()
_GUARD = threading.Lock()


def _name(name):
    text = str(name).strip().lower() if isinstance(name, (str, int)) and not isinstance(name, bool) else ''
    if not _NAME.fullmatch(text):
        raise ValueError('invalid Pokémon name or id')
    return text


def parse(source, url=None):
    """The normalized record for PokéAPI pokemon JSON (text or an already-parsed object)."""
    if isinstance(source, str):
        raw = source.encode('utf-8')
        if len(raw) > LIMIT:
            raise ValueError('source exceeds 2 MiB')
        try:
            data = json.loads(source)
        except ValueError:
            raise ValueError('source is not JSON') from None
    elif isinstance(source, dict):
        data = source
        raw = json.dumps(source, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        if len(raw) > LIMIT:
            raise ValueError('source exceeds 2 MiB')
    else:
        raise ValueError('source is JSON text or an object')
    try:
        stats = {row['stat']['name']: int(row['base_stat']) for row in data['stats']}
        record = {
            'schema': 'pokeapi-pokemon/1', 'id': int(data['id']), 'name': str(data['name']),
            'height_m': int(data['height']) / 10, 'weight_kg': int(data['weight']) / 10,
            'base_experience': data.get('base_experience'),
            'types': [row['type']['name'] for row in sorted(data['types'], key=lambda r: r['slot'])],
            'abilities': [{'name': row['ability']['name'], 'hidden': bool(row['is_hidden'])}
                          for row in sorted(data['abilities'], key=lambda r: r['slot'])],
            'stats': stats}
    except (KeyError, TypeError, ValueError):
        raise ValueError('not a PokéAPI pokemon response') from None
    if not _NAME.fullmatch(record['name']) or not stats:
        raise ValueError('not a PokéAPI pokemon response')
    md = load_markdown()
    if md is not None:
        record['markdown'] = (f"# {record['name']} (#{record['id']})\n\n"
                              + md.table(['type', 'height m', 'weight kg'], [[', '.join(record['types']), record['height_m'], record['weight_kg']]]) + '\n'
                              + md.table(['stat', 'base'], list(stats.items())) + '\n'
                              + md.table(['ability', 'hidden'], [[a['name'], 'yes' if a['hidden'] else ''] for a in record['abilities']]))
    record['source'] = {'url': url, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    return record


def pokemon_source(source, url=None):
    """Offline: JSON copied from the browser's view-source / raw tab, or the object itself."""
    if url is not None and (not isinstance(url, str) or not url.startswith('https://pokeapi.co/api/v2/pokemon/')):
        raise ValueError('url, when given, is the pokeapi.co pokemon URL the text came from')
    return parse(source, url)


def pokemon(name):
    """One GET of ``/api/v2/pokemon/<name or id>``; repeats of a name reuse the bounded cache."""
    key = _name(name)
    with _GUARD:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return copy.deepcopy(_CACHE[key])
    from ..source_access import fetch_bytes
    url = API.format(name=key)
    raw, charset = fetch_bytes(url, accept='application/json', content_types={'application/json'}, label='JSON', limit=LIMIT)
    record = parse(raw.decode(charset or 'utf-8'), url)
    with _GUARD:
        _CACHE[key] = record
        while len(_CACHE) > CACHE_MAXSIZE:
            _CACHE.popitem(last=False)
    return copy.deepcopy(record)
