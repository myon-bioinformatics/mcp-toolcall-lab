"""Build the legacy Pages entry point after portfolio publication lands."""
from pathlib import Path
import sys

TARGET = 'https://myon-bioinformatics.github.io/tools/mcp-toolcall-lab/'


def write_redirect(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.html').write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Tool lab moved to portfolio</title></head><body>'
        f'<p>Wiki, Pixiv and the public report moved to <a href="{TARGET}">the portfolio</a>.</p>'
        '<script>location.replace(' + repr(TARGET) + ' + location.search + location.hash);</script>'
        '</body></html>\n', encoding='utf-8')


if __name__ == '__main__':
    write_redirect(Path(sys.argv[1]))
