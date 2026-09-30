"""Upstream markdown#62 regressions through the lab's actual rendering path."""

import pytest

from mcp_toolcall_lab.markdown_lib import load_markdown
from mcp_toolcall_lab.stub_front import _assistant_html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("keep \x00PH0\x00 here and `code`", "<p>keep \ufffdPH0\ufffd here and <code>code</code></p>\n"),
        ("only \x00PH0\x00 text", "<p>only \ufffdPH0\ufffd text</p>\n"),
        ("```\n\x00\n```", "<pre><code>\ufffd\n</code></pre>\n"),
    ],
)
def test_vendored_markdown_and_assistant_replace_input_nul(source, expected):
    md = load_markdown()
    assert md is not None
    for result in (md.markdown_to_html(source), _assistant_html(source)):
        assert result == expected
        assert "\x00" not in result
