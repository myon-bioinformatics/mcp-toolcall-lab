from extract import extract


def test_article_content_links_and_styles_without_network():
    html = '''<title>Public &amp; test</title><link rel="stylesheet" href="/a.css">
    <nav>ignore</nav><main><article class="markdown-body"><h1>January</h1>
    <p>1–31 <b>days</b></p><a href="/calendar">Calendar</a>
    <script>do_not_execute()</script><a href="javascript:bad()">bad</a>
    </article></main>'''
    result = extract(html, 'https://example.test/repo')
    assert result['scope'] == 'article'
    assert result['title'] == 'Public & test'
    assert '1–31 days' in result['text']
    assert 'do_not_execute' not in result['text']
    assert 'ignore' not in result['text']
    assert result['links'] == [{'text': 'Calendar', 'url': 'https://example.test/calendar'}]
    assert result['stylesheets'] == ['https://example.test/a.css']


def test_fallback_and_class_change():
    for html, scope in [('<main><h2>A</h2></main>', 'main'), ('<p>A</p>', 'document'),
                        ('<article class="new-name"><h2>A</h2></article>', 'article')]:
        result = extract(html, 'https://example.test/')
        assert result['scope'] == scope
        assert result['text'] == 'A'


def test_all_links_retained_without_following_and_repeatable():
    html = '<article>' + ''.join(f'<a href="/{i}">{i}</a>' for i in range(1, 32)) + '</article>'
    first = extract(html, 'https://example.test/')
    assert len(first['links']) == 31
    assert first == extract(html, 'https://example.test/')
