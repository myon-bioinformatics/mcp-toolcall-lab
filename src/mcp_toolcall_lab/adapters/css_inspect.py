"""Bounded offline CSS source inspection, not a cascade or rendering engine.

Keep declarations, conditional contexts and unsupported constructs observable.
Never load @import, url(), stylesheets, fonts or scripts.
"""
import re
import hashlib
from urllib.parse import urljoin

MAX_BYTES = 2 * 1024 * 1024


def _clean_comments(text):
    out, i, quote = [], 0, None
    while i < len(text):
        char = text[i]
        if char == '\\':
            out.append(text[i:i + 2])
            i += 2
            continue
        if quote:
            out.append(char)
            if char == quote:
                quote = None
        elif char in '\"\'':
            quote = char
            out.append(char)
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('unterminated CSS comment')
            out.append(' ')
            i = end + 2
            continue
        else:
            out.append(char)
        i += 1
    if quote:
        raise ValueError('unterminated CSS string')
    return ''.join(out)


def _delimiters(text, delimiters):
    """Yield only delimiters outside strings and parentheses/brackets."""
    stack, quote, i = [], None, 0
    while i < len(text):
        char = text[i]
        if char == '\\':
            i += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in '\"\'':
            quote = char
        elif char in '([':
            stack.append(char)
            if len(stack) > 128:
                raise ValueError('CSS nesting exceeds 128 levels')
        elif char in ')]':
            if not stack or stack.pop() != {')': '(', ']': '['}[char]:
                raise ValueError('unbalanced CSS expression')
        elif not stack and char in delimiters:
            yield i, char
        i += 1
    if quote or stack:
        raise ValueError('unterminated CSS expression')


def _split(text, delimiter):
    start, result = 0, []
    for index, _ in _delimiters(text, delimiter):
        result.append(text[start:index].strip())
        start = index + 1
    result.append(text[start:].strip())
    return result


def declarations(text):
    result, unknown = [], []
    for item in _split(_clean_comments(text), ';'):
        if not item:
            continue
        colon = next(_delimiters(item, ':'), None)
        if colon is None:
            unknown.append(item)
            continue
        index = colon[0]
        name, value = item[:index].strip(), item[index + 1:].strip()
        if not re.fullmatch(r'--[\w-]+|[a-zA-Z][\w-]*', name, re.ASCII) or not value:
            unknown.append(item)
            continue
        important = bool(re.search(r'!\s*important\s*$', value, re.I))
        if important:
            value = re.sub(r'!\s*important\s*$', '', value, flags=re.I).rstrip()
        # Quoted strings and url() fragments are not color literals.
        color_source = re.sub(r'url\([^)]*\)|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                              '', value, flags=re.I)
        colors = re.findall(r'#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{4}\b|#[0-9a-fA-F]{3}\b', color_source)
        result.append({'property': name if name.startswith('--') else name.lower(),
                       'value': value, 'important': important, 'hex_colors': colors,
                       'variables': re.findall(r'var\(\s*(--[\w-]+)', value, re.ASCII)})
    return {'declarations': result, 'unknown': unknown}


def inspect_css(css):
    if not isinstance(css, str) or len(css.encode('utf-8')) > MAX_BYTES:
        raise ValueError('CSS must be a string of at most 2 MiB')
    cleaned = _clean_comments(css)
    rules, at_rules, unknown = [], [], []

    def read(text, contexts=()):
        if len(contexts) > 32:
            raise ValueError('CSS block nesting exceeds 32 levels')
        start, depth, opening, header = 0, 0, None, None
        for index, char in _delimiters(text, '{};'):
            if char == '{':
                if depth == 0:
                    header, opening = text[start:index].strip(), index
                depth += 1
                if depth > 128:
                    raise ValueError('CSS block nesting exceeds 128 levels')
            elif char == '}':
                depth -= 1
                if depth < 0:
                    raise ValueError('unbalanced CSS block')
                if depth == 0:
                    body = text[opening + 1:index]
                    if header.startswith('@'):
                        name = header.split(None, 1)[0].lower()
                        at_rules.append({'header': header, 'body': body, 'contexts': list(contexts)})
                        if name in {'@media', '@supports', '@layer', '@container'}:
                            read(body, (*contexts, header))
                        else:
                            unknown.append({'source': header, 'reason': 'unsupported at-rule body'})
                    elif header:
                        if any(c in body for _, c in _delimiters(body, '{}')):
                            unknown.append({'source': header, 'reason': 'nested declaration block'})
                        else:
                            rules.append({'selectors': _split(header, ','), 'contexts': list(contexts),
                                          **declarations(body)})
                    else:
                        unknown.append({'source': body, 'reason': 'missing selector'})
                    start = index + 1
            elif depth == 0:
                statement = text[start:index].strip()
                if statement.startswith('@'):
                    at_rules.append({'header': statement, 'body': None, 'contexts': list(contexts)})
                elif statement:
                    unknown.append({'source': statement, 'reason': 'statement without block'})
                start = index + 1
        if depth:
            raise ValueError('unterminated CSS block')
        if text[start:].strip():
            unknown.append({'source': text[start:].strip(), 'reason': 'trailing source without block'})

    read(cleaned)
    return {'schema': 'css-inspection/1', 'content_sha256': hashlib.sha256(css.encode('utf-8')).hexdigest(),
            'rules': rules, 'at_rules': at_rules,
            'unknown': unknown, 'computed_styles': False}


def inspect_html_styles(html, url, *, stylesheets=None):
    """Inspect embedded/inline CSS plus caller-supplied saved stylesheet text."""
    from .html_snapshot import Parser, select_nodes, MAX_BYTES as HTML_LIMIT
    if not isinstance(html, str) or len(html.encode('utf-8')) > HTML_LIMIT:
        raise ValueError('HTML must be a string of at most 2 MiB')
    if stylesheets is not None and not isinstance(stylesheets, dict):
        raise ValueError('stylesheets must map saved URLs to CSS strings')
    if stylesheets is not None and len(stylesheets) > 128:
        raise ValueError('at most 128 saved stylesheets are supported')
    parser = Parser()
    parser.feed(html)
    nodes = list(parser.root.walk())
    sources, inline, refs = [], [], []

    def all_nodes(node):
        if node.tag in {'script', 'template', 'noscript'}:
            return
        yield node
        for child in node.children:
            if hasattr(child, 'tag'):
                yield from all_nodes(child)

    for index, node in enumerate(all_nodes(parser.root)):
        if node.tag == 'style':
            css = ''.join(child for child in node.children if isinstance(child, str))
            sources.append({'kind': 'embedded', 'node': index, 'media': node.attrs.get('media'),
                            'inspection': inspect_css(css)})
        if node.attrs.get('style') is not None and node in nodes:
            inline.append({'node': index, 'tag': node.tag, 'id': node.attrs.get('id'),
                           **declarations(node.attrs['style'])})
        if node.tag == 'link' and 'stylesheet' in (node.attrs.get('rel') or '').lower().split():
            href = node.attrs.get('href')
            if href:
                refs.append(urljoin(url, href))
    for href, css in (stylesheets or {}).items():
        if not isinstance(href, str):
            raise ValueError('stylesheet URLs must be strings')
        sources.append({'kind': 'supplied', 'url': urljoin(url, href), 'inspection': inspect_css(css)})
    if len(sources) > 128:
        raise ValueError('at most 128 CSS sources are supported')
    matches = []
    for source_index, source in enumerate(sources):
        for rule_index, rule in enumerate(source['inspection']['rules']):
            for selector in rule['selectors']:
                try:
                    count = len(select_nodes(nodes, selector))
                    matches.append({'source': source_index, 'rule': rule_index, 'selector': selector,
                                    'matched': count, 'contexts': rule['contexts'],
                                    'status': 'matched' if count else 'unmatched'})
                except ValueError as exc:
                    matches.append({'source': source_index, 'rule': rule_index, 'selector': selector,
                                    'matched': None, 'status': 'unsupported', 'reason': str(exc)})
    supplied = {source['url'] for source in sources if source['kind'] == 'supplied'}
    imports = [rule['header'] for source in sources for rule in source['inspection']['at_rules']
               if re.match(r'@import\b', rule['header'], re.I)]
    return {'schema': 'html-css-inspection/1', 'sources': sources, 'inline': inline,
            'stylesheet_references': refs, 'unloaded_stylesheets': [ref for ref in refs if ref not in supplied],
            'unloaded_imports': imports,
            'selector_matches': matches, 'computed_styles': False}
