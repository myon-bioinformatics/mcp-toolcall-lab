"""Read-only source dispatch and explicit local snapshot capture; no MCP dependency."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .adapters.html_snapshot import MAX_BYTES, extract


class Registry:
    """Trusted handlers only; request JSON cannot import or evaluate code."""

    def __init__(self):
        self._operations = {}

    def register(self, provider, operation, handler, *, network=False):
        key = (provider, operation)
        if key in self._operations:
            raise ValueError('duplicate source operation')
        self._operations[key] = (handler, network)

    def invoke(self, provider, operation, arguments, *, allow_network=False):
        if not isinstance(provider, str) or not isinstance(operation, str) or not isinstance(arguments, dict):
            raise ValueError('provider/operation must be strings and arguments an object')
        if not all(isinstance(key, str) for key in arguments):
            raise ValueError('argument names must be strings')
        if type(allow_network) is not bool:
            raise ValueError('allow_network must be boolean')
        try:
            handler, network = self._operations[(provider, operation)]
        except KeyError:
            raise ValueError('unknown source operation') from None
        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError:
            raise ValueError('invalid operation arguments') from None
        if network and not allow_network:
            raise ValueError('network operation requires explicit allow_network')
        return {'provider': provider, 'operation': operation, 'data': handler(**arguments)}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _url(url):
    if not isinstance(url, str):
        raise ValueError('source URL must be a string')
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('source URL must be HTTP(S) without embedded credentials')
    return url


def fetch_snapshot(url, *, opener=None):
    """One bounded GET, no retries/redirects/assets; explicit local use only.

    Not registered in the dispatcher or MCP: this is not a remote URL proxy.
    Charset comes from Content-Type or defaults to UTF-8 (strict decoding).
    """
    request = Request(_url(url), headers={
        'User-Agent': 'mcp-toolcall-lab/source-snapshot',
        'Accept': 'text/html,application/xhtml+xml', 'Accept-Encoding': 'identity'})
    open_request = opener or build_opener(_NoRedirect()).open
    with open_request(request, timeout=15) as response:
        if response.status != 200:
            raise ValueError('expected HTTP 200')
        if response.headers.get_content_type() not in {'text/html', 'application/xhtml+xml'}:
            raise ValueError('response is not HTML')
        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            raise ValueError('compressed response is not supported')
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('HTML response exceeds 2 MiB')
        encoding = response.headers.get_content_charset() or 'utf-8'
        html = raw.decode(encoding)
    return make_snapshot(html, url, fetched_at=datetime.now(timezone.utc).isoformat(),
                         encoding=encoding, response_sha256=hashlib.sha256(raw).hexdigest())


def make_snapshot(html, url, *, fetched_at=None, encoding='utf-8', response_sha256=None):
    if not isinstance(html, str) or len(html.encode('utf-8')) > MAX_BYTES:
        raise ValueError('HTML must be a string of at most 2 MiB')
    return {'schema': 'html-snapshot/1', 'url': _url(url), 'html': html,
            'fetched_at': fetched_at, 'encoding': encoding,
            'response_sha256': response_sha256,
            'content_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest()}


def extract_snapshot(snapshot, profile='generic'):
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 'html-snapshot/1':
        raise ValueError('expected html-snapshot/1')
    checked = make_snapshot(snapshot.get('html'), snapshot.get('url'))
    if checked['content_sha256'] != snapshot.get('content_sha256'):
        raise ValueError('snapshot content hash mismatch')
    return {'snapshot': {k: snapshot.get(k) for k in
                         ('schema', 'url', 'fetched_at', 'content_sha256', 'response_sha256', 'encoding')},
            'extraction': extract(snapshot['html'], snapshot['url'], profile)}


def default_registry():
    from .adapters.wikipedia import article
    registry = Registry()
    registry.register('html', 'extract', extract_snapshot)
    registry.register('wikipedia', 'article', article, network=True)
    return registry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path, help='JSON request, or new snapshot destination with --fetch-url')
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--fetch-url', help='GET once and create snapshot; existing files are never overwritten')
    args = parser.parse_args(argv)
    try:
        if args.fetch_url:
            if not args.allow_network:
                raise ValueError('--fetch-url requires --allow-network')
            if args.request.exists():
                raise ValueError('snapshot destination already exists')
            snapshot = fetch_snapshot(args.fetch_url)
            with args.request.open('x', encoding='utf-8') as output:
                json.dump(snapshot, output, ensure_ascii=False)
            result = {k: v for k, v in snapshot.items() if k != 'html'}
        else:
            with args.request.open('rb') as source:
                raw = source.read(MAX_BYTES * 6 + 1)
            if len(raw) > MAX_BYTES * 6:
                raise ValueError('request JSON too large')
            request = json.loads(raw)
            if not isinstance(request, dict) or set(request) != {'provider', 'operation', 'arguments'}:
                raise ValueError('expected provider, operation, arguments')
            result = default_registry().invoke(**request, allow_network=args.allow_network)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
        return 0
    except (ValueError, TypeError, OSError, LookupError, RuntimeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
