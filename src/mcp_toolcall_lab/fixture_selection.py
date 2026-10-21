"""Bounded-to-one-directory, stdlib-only selection for offline replay fixtures."""
from __future__ import annotations

import fnmatch
from pathlib import Path


def fixture_paths(directory, *, patterns=('*',), exclude=()):
    """Select JSON basenames/stems with case-sensitive ORs, then exclusions."""
    if isinstance(patterns, str) or isinstance(exclude, str):
        raise TypeError('fixture rules must be sequences')
    patterns, exclude = tuple(patterns), tuple(exclude)
    if any(not isinstance(rule, str) or not rule for rule in patterns + exclude):
        raise ValueError('fixture rules must be nonempty strings')
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('fixture root must be a directory, not a symlink')
    def matches(path, rules):
        return any(fnmatch.fnmatchcase(path.name, rule) or fnmatch.fnmatchcase(path.stem, rule) for rule in rules)
    paths = [p for p in sorted(root.glob('*.json')) if not p.is_symlink() and p.is_file()
             and matches(p, patterns) and not matches(p, exclude)]
    if not paths:
        raise FileNotFoundError(f'no selected JSON fixtures in {root}')
    return paths


def selection_arguments(parser):
    parser.add_argument('--case', action='append', help='fixture filename/stem glob; repeat for OR')
    parser.add_argument('--exclude', action='append', help='exclude fixture filename/stem glob')
    parser.add_argument('--list', action='store_true', help='list selected fixture paths without reading or replaying them')


def list_selection(args):
    import json
    if args.out:
        raise ValueError('--list cannot be combined with --out')
    paths = fixture_paths(args.fixtures, patterns=args.case or ('*',), exclude=args.exclude or ())
    print(json.dumps({'cases': [p.name for p in paths]}))
