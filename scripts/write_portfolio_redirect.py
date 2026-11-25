"""Build the legacy Pages entry point after portfolio publication lands."""
from pathlib import Path
import argparse

TARGET = 'https://myon-bioinformatics.github.io/tools/mcp/'


def write_redirect(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.html').write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="icon" type="image/png" href="execution/assets/mcp-toolcall-lab-favicon-artwork.png">'
        '<link rel="apple-touch-icon" href="execution/assets/mcp-toolcall-lab-favicon-artwork.png">'
        '<title>Tool lab moved to portfolio</title></head><body>'
        '<img src="execution/assets/mcp-toolcall-lab-logo.png" width="138" height="150" alt="MCP Toolcall Lab logo">'
        f'<p>Wiki, Pixiv, Pokémon and the public report moved to <a href="{TARGET}">the portfolio</a>.</p>'
        '<script>location.replace(' + repr(TARGET) + ' + location.search + location.hash);</script>'
        '</body></html>\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path, help='directory to write index.html into')
    write_redirect(parser.parse_args().out)
