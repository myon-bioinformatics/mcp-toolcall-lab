"""Compatibility CLI for the original snapshot experiment (no network)."""
import argparse
import json
from pathlib import Path
from mcp_toolcall_lab.adapters.html_snapshot import extract
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--url', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(extract(args.snapshot.read_text(encoding='utf-8'), args.url), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    raise SystemExit(main())
