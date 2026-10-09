"""Offline HTML snapshot inspection; stdlib only, no network or script execution."""
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 2 * 1024 * 1024
EXCLUDED = {'script', 'style', 'template', 'noscript'}


class Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self):
        if self.tag in EXCLUDED:
            return
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        if self.tag in {'script', 'style', 'template', 'noscript'}:
            return ''
        return ' '.join(c.text() if isinstance(c, Node) else c for c in self.children)


class Parser(HTMLParser):
    VOID = set('area base br col embed hr img input link meta param source track wbr'.split())

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('document')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if len(self.stack) >= 128:
            raise ValueError('HTML nesting exceeds 128 levels')
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def clean(node):
    return ' '.join(node.text().split())


def extract(html, url, profile='generic'):
    """Extract this document without network or browser HTML5 tree building."""
    if not isinstance(html, str) or len(html.encode('utf-8')) > MAX_BYTES:
        raise ValueError('HTML must be a string of at most 2 MiB')
    if not isinstance(url, str) or urlsplit(url).scheme not in {'http', 'https'} or not urlsplit(url).netloc:
        raise ValueError('source URL must be absolute HTTP(S)')
    if profile not in {'generic', 'github-readme'}:
        raise ValueError('unknown HTML profile')
    parser = Parser()
    parser.feed(html)
    nodes = list(parser.root.walk())
    # Ordered structural fallbacks reuse one parsed document, never another GET.
    scopes = [n for n in nodes if n.tag == 'article']
    selected = 'article'
    if profile == 'github-readme':
        scopes = [n for n in scopes if 'markdown-body' in (n.attrs.get('class') or '').split()]
        if len(scopes) != 1:
            raise ValueError('github-readme requires exactly one article.markdown-body')
    elif not scopes:
        scopes = [n for n in nodes if n.tag == 'main'] or [parser.root]
        selected = 'main' if scopes[0] is not parser.root else 'document'
    descendants = {id(child) for root in scopes for child in list(root.walk())[1:]}
    scopes = [n for n in scopes if id(n) not in descendants]
    content_nodes = [n for scope in scopes for n in scope.walk()]
    links = []
    for node in content_nodes:
        href = node.attrs.get('href')
        if node.tag == 'a' and href:
            absolute = urljoin(url, href)
            if urlsplit(absolute).scheme in {'http', 'https'}:
                links.append({'text': clean(node), 'url': absolute})
    text = ' '.join(filter(None, (clean(scope) for scope in scopes)))
    if not text:
        raise ValueError('selected HTML scope has no text')
    return {
        'source_url': url, 'profile': profile, 'scope': selected,
        'title': next((clean(n) for n in nodes if n.tag == 'title'), ''),
        'headings': [{'level': n.tag, 'text': clean(n)} for n in content_nodes
                     if n.tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}],
        'text': text, 'links': links,
        'structure': [{'tag': n.tag, 'id': n.attrs.get('id'), 'class': n.attrs.get('class')}
                      for n in nodes if n.tag in {'main', 'article'}],
        'tag_counts': dict(Counter(n.tag for n in nodes)),
        'stylesheets': [urljoin(url, n.attrs['href']) for n in nodes
                        if n.tag == 'link' and 'stylesheet' in (n.attrs.get('rel') or '').split()
                        and n.attrs.get('href') and urlsplit(urljoin(url, n.attrs['href'])).scheme in {'http', 'https'}],
    }
