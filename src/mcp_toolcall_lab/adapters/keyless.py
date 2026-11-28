"""Keyless public APIs, one reader each: 🎬 YouTube oEmbed, 🕸️ Wikidata (search, an item), 🧬 UniProtKB, 🐙 GitHub.

A reader takes the tool arguments and ``get(url) -> text`` and returns a record; ``markdown(name, record)`` is the same
record to read. The caller owns the transport: a page's XMLHttpRequest (these APIs send CORS headers, so a browser can
read them), or ``source_access.fetch_bytes`` behind ``allow_network``. No network work occurs at import, and there are no
keys or tokens: GitHub without one answers 60 requests an hour from one address. Arguments are checked before any request.

``TOOLS`` gives each reader as an MCP tool (description, inputSchema) with ``cues``: words that name it, for a router that
has no family for it yet. ``BROWSER_BLOCKED`` is the exception list: providers of this repository a browser cannot read,
and why; a host keeps those on a server (or leaves them out).
"""
from __future__ import annotations

import json, re
from typing import Any, Callable
from urllib.parse import urlencode

from ..markdown_lib import load_markdown

WIKIDATA = 'https://www.wikidata.org/w/api.php?'
BROWSER_BLOCKED = {
    'niconico': 'Snapshot Search API v2 answers without Access-Control-Allow-Origin (checked 2026-10-21)',
    'pixiv': 'pixiv Encyclopedia pages are HTML without CORS headers',
}


def _q(**params: Any) -> str:
    return urlencode({key: value for key, value in params.items() if value is not None})


def youtube_oembed(args: dict, get: Callable[[str], str]) -> dict:
    """🎬 One video URL: title, channel and thumbnail (the embed HTML is left out)."""
    url = str(args['url']).strip()
    if not re.match(r'https://(?:www\.|m\.|music\.)?(youtube\.com|youtu\.be)/', url):
        raise ValueError('a https://www.youtube.com/… or https://youtu.be/… URL')
    data = json.loads(get('https://www.youtube.com/oembed?' + _q(url=url, format='json')))
    return {'schema': 'youtube-oembed/1', 'url': url, **{key: data.get(key) for key in (
        'title', 'author_name', 'author_url', 'thumbnail_url', 'thumbnail_width', 'thumbnail_height', 'provider_name')}}


def wikidata_search(args: dict, get: Callable[[str], str]) -> dict:
    """🕸️ Items whose label matches (wbsearchentities): id, label, description."""
    lang = args.get('lang', 'ja')
    data = json.loads(get(WIKIDATA + _q(action='wbsearchentities', search=args['query'], language=lang, uselang=lang,
                                        type='item', limit=5, format='json', origin='*')))
    return {'schema': 'wikidata-search/1', 'query': args['query'], 'lang': lang, 'items': [
        {'id': hit['id'], 'label': hit.get('label', ''), 'description': hit.get('description', ''),
         'url': f'https://www.wikidata.org/wiki/{hit["id"]}'} for hit in data.get('search', [])]}


def wikidata_entity(args: dict, get: Callable[[str], str]) -> dict:
    """🕸️ One item as a node of a knowledge graph: label, description, aliases, and what it is an instance of (P31) and a
    subclass of (P279), labelled by one more request."""
    qid, lang = str(args['id']).strip().upper(), args.get('lang', 'ja')
    if not re.fullmatch(r'Q[1-9][0-9]{0,11}', qid):
        raise ValueError('an item id such as Q42')
    langs = '|'.join(dict.fromkeys((lang, 'en')))
    entity = json.loads(get(WIKIDATA + _q(action='wbgetentities', ids=qid, props='labels|descriptions|aliases|claims',
                                          languages=langs, format='json', origin='*')))['entities'][qid]
    if 'missing' in entity:
        raise LookupError(f'no Wikidata item {qid}')
    pick = lambda field: next((entity.get(field, {})[code]['value'] for code in (lang, 'en') if code in entity.get(field, {})), '')
    links = {prop: [c['mainsnak']['datavalue']['value']['id'] for c in entity.get('claims', {}).get(prop, [])
                    if c.get('mainsnak', {}).get('snaktype') == 'value'][:8] for prop in ('P31', 'P279')}
    ids, names = sorted({q for qs in links.values() for q in qs}), {}
    if ids:
        found = json.loads(get(WIKIDATA + _q(action='wbgetentities', ids='|'.join(ids), props='labels', languages=langs,
                                             format='json', origin='*')))['entities']
        names = {q: next((v['labels'][c]['value'] for c in (lang, 'en') if c in v.get('labels', {})), q) for q, v in found.items()}
    return {'schema': 'wikidata-entity/1', 'id': qid, 'url': f'https://www.wikidata.org/wiki/{qid}', 'label': pick('labels'),
            'description': pick('descriptions'), 'aliases': [a['value'] for a in entity.get('aliases', {}).get(lang, [])][:8],
            **{key: [{'id': q, 'label': names.get(q, q)} for q in links[prop]]
               for key, prop in (('instance_of', 'P31'), ('subclass_of', 'P279'))}}


def uniprot_search(args: dict, get: Callable[[str], str]) -> dict:
    """🧬 UniProtKB entries for a query: accession, protein, organism, genes, length."""
    data = json.loads(get('https://rest.uniprot.org/uniprotkb/search?' + _q(
        query=args['query'], format='json', size=args.get('size', 5), fields='accession,protein_name,organism_name,gene_names,length')))
    rows = []
    for hit in data.get('results', []):
        protein = hit.get('proteinDescription', {})
        name = (protein.get('recommendedName') or next(iter(protein.get('submissionNames', [])), {})).get('fullName', {}).get('value', '')
        rows.append({'accession': hit['primaryAccession'], 'protein': name, 'organism': hit.get('organism', {}).get('scientificName', ''),
                     'genes': [g['geneName']['value'] for g in hit.get('genes', []) if 'geneName' in g],
                     'length': hit.get('sequence', {}).get('length'), 'url': f'https://www.uniprot.org/uniprotkb/{hit["primaryAccession"]}'})
    return {'schema': 'uniprot-search/1', 'query': args['query'], 'entries': rows}


def github_repo(args: dict, get: Callable[[str], str]) -> dict:
    """🐙 One public repository: description, language, topics, stars, forks, issues, license, last push."""
    repo = str(args['repo']).strip().removeprefix('https://github.com/').strip('/')
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}', repo):
        raise ValueError('OWNER/NAME')
    data = json.loads(get(f'https://api.github.com/repos/{repo}'))
    return {'schema': 'github-repo/1', 'repo': data['full_name'], 'url': data['html_url'], 'description': data.get('description') or '',
            'language': data.get('language'), 'topics': data.get('topics', []), 'stars': data.get('stargazers_count'),
            'forks': data.get('forks_count'), 'open_issues': data.get('open_issues_count'), 'archived': data.get('archived'),
            'license': (data.get('license') or {}).get('spdx_id'), 'pushed_at': data.get('pushed_at')}


def _table(head: list[str], rows: list[list]) -> str:
    """A table from the vendored markdown.py (the one-parser rule of ``wikipedia_tool`` and ``pokeapi``); 0 件 for no rows."""
    if not rows:
        return '0 件'
    return load_markdown().table(head, [[' '.join(str('—' if v is None else v).split()) for v in row] for row in rows]).strip()


def markdown(name: str, r: dict) -> str:
    """The record of tool ``name`` to read: a heading with its link, then a table (the record as JSON without markdown.py)."""
    if load_markdown() is None:
        return json.dumps(r, ensure_ascii=False)
    if name == 'fetch_youtube_oembed':
        return f'### 🎬 [{r["title"]}]({r["url"]})\n\n' + _table(['チャンネル', 'サムネ'], [[f'[{r["author_name"]}]({r["author_url"]})', r['thumbnail_url']]])
    if name == 'search_wikidata':
        return _table(['ID', 'ラベル', '説明'], [[f'[{i["id"]}]({i["url"]})', i['label'], i['description']] for i in r['items']])
    if name == 'fetch_wikidata_entity':
        return (f'### 🕸️ [{r["label"] or r["id"]}]({r["url"]}) {r["description"]}\n\n'
                + _table(['関係', '相手'], [[rel, ' · '.join(f'{x["label"]} ({x["id"]})' for x in r[key]) or '—']
                                          for rel, key in (('P31 instance of', 'instance_of'), ('P279 subclass of', 'subclass_of'))]))
    if name == 'search_uniprot':
        return _table(['アクセッション', 'タンパク質', '生物種', '遺伝子', '長さ'], [
            [f'[{e["accession"]}]({e["url"]})', e['protein'], e['organism'], ', '.join(e['genes']), e['length']] for e in r['entries']])
    return (f'### 🐙 [{r["repo"]}]({r["url"]}) {r["description"]}\n\n'
            + _table(['言語', '⭐', '🍴', 'issue', 'license', 'push'],
                     [[r['language'], r['stars'], r['forks'], r['open_issues'], r['license'], (r['pushed_at'] or '')[:10]]]))


def _schema(props: dict, required: list[str]) -> dict:
    return {'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False}


# name → (reader, description, inputSchema, cues)
TOOLS = {
    'fetch_youtube_oembed': (youtube_oembed, 'YouTube の動画 URL から題名・チャンネル・サムネ（oEmbed、キー不要）',
                             _schema({'url': {'type': 'string'}}, ['url']), ('youtube', 'youtu.be', 'ユーチューブ')),
    'search_wikidata': (wikidata_search, 'Wikidata の項目を名前で探す（ID・ラベル・説明）',
                        _schema({'query': {'type': 'string'}, 'lang': {'type': 'string'}}, ['query']), ('wikidata', 'ウィキデータ')),
    'fetch_wikidata_entity': (wikidata_entity, 'Wikidata の項目 1 つ: ラベル・説明・別名と、何の一種か（P31 instance of / P279 subclass of）',
                              _schema({'id': {'type': 'string'}, 'lang': {'type': 'string'}}, ['id']), ('wikidata', 'ウィキデータ')),
    'search_uniprot': (uniprot_search, 'UniProtKB でタンパク質を探す（アクセッション・名前・生物種・遺伝子・長さ）',
                       _schema({'query': {'type': 'string'}, 'size': {'type': 'integer'}}, ['query']), ('uniprot', 'タンパク質', 'protein', 'アミノ酸')),
    'fetch_github_repo': (github_repo, '公開 GitHub リポジトリの概要（説明・言語・スター・更新）',
                          _schema({'repo': {'type': 'string', 'description': 'OWNER/NAME'}}, ['repo']), ('github', 'ギットハブ')),
}


def read(name: str, args: dict, get: Callable[[str], str]) -> dict:
    """Run tool ``name`` (a ``TOOLS`` key) with ``get``: its record."""
    return TOOLS[name][0](args, get)
