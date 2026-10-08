"""Transferred callbacks run offline against real pinned helpers, without Gradio."""
import importlib.util
import pytest
from mcp_toolcall_lab import gradio_galleria as gallery


def test_shapes_and_templates():
    assert gallery._render_shape('Square', 2, '*') == '**\n**'
    assert gallery._render_shape('Triangle', 2, '*')
    assert gallery._render_shape('Diamond', 2, '*')
    assert gallery._get_template(gallery.list_templates()[0])


def test_markdown_roundtrip_and_headings(tmp_path):
    path = tmp_path / '日本語.md'
    gallery._save_md('# Hello\n## 子\n', str(path))
    text, info = gallery._read_md(str(path), True)
    assert text == '# Hello\n## 子\n'
    assert '3' in info
    assert gallery._extract_sections_ui(text) == '# Hello\n  ## 子'
    assert gallery._read_md('', False) == ('', 'Please specify a file path.')
    assert gallery._save_md('x', '') == 'Please specify a file path.'


def test_viewer_formats_and_failures(tmp_path):
    csv = tmp_path / 'table.csv'
    csv.write_text('name,note\n日本語,"a,b"\n', encoding='utf-8')
    assert gallery._load_for_display('csv', {'csv': csv}) == (
        'csv', '', {'headers': ['name', 'note'], 'data': [['日本語', 'a,b']]})
    js = tmp_path / 'data.json'
    js.write_text('{"a":1}', encoding='utf-8')
    assert gallery._load_for_display('json', {'json': js})[1] == '{\n  "a": 1\n}'
    assert gallery._read_text(js, size_limit_bytes=1).startswith('[too_large]')
    assert gallery._read_text(tmp_path / 'missing').startswith('[missing]')
    assert gallery._load_for_display('missing', {})[1].startswith('[error]')
    assert gallery._format_json('invalid') == 'invalid'


def test_scan_includes_json_and_excludes_private_and_external_files(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, 'BASE_DIR', tmp_path)
    (tmp_path / 'public.json').write_text('{}')
    for folder in ('.git', '.venv', 'vendor', 'node_modules'):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / 'private.md').write_text('private')
    (tmp_path / 'alias.md').symlink_to(tmp_path / '.git/private.md')
    assert list(gallery._scan_repo_files()) == ['public.json']


@pytest.mark.skipif(importlib.util.find_spec('gradio') is None, reason='optional galleria extra')
def test_actual_gradio_build_and_updates():
    demo = gallery.build_ui()
    assert len(demo.config['dependencies']) >= 8
    assert gallery._to_view_updates('csv', csv_value={'headers': ['x'], 'data': [['y']]})[2]['value'] == [['y']]
    assert gallery._to_view_updates('markdown', '# Hi')[1]['visible'] is True
    assert gallery._to_view_updates('text', 'Hi')[0]['value'] == 'Hi'
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import gradio as gr
    app = gr.mount_gradio_app(FastAPI(), demo, path="/galleria", css=gallery.TERMINAL_CSS)
    with TestClient(app) as client:
        assert client.get('/galleria/').status_code == 200
        config = client.get('/galleria/config')
        assert config.status_code == 200
        assert config.json()['dependencies']
    demo.close()
