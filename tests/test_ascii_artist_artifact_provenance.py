import ast
from datetime import datetime
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "vendor" / "ascii_artist.py"
HEADER_RE = re.compile(
    r"^# metadata: __all__=(?P<count>[0-9]+) \| "
    r"base_sha=(?P<sha>(?:[0-9a-f]{40}|[0-9a-f]{64})) \| "
    r"updated_at=(?P<updated_at>\S+)$"
)


def test_vendored_ascii_artist_provenance_matches_literal_all():
    source = ARTIFACT.read_text(encoding="utf-8")
    header = next(
        (line for line in source.splitlines()[:8] if line.startswith("# metadata:")),
        None,
    )
    assert header is not None
    match = HEADER_RE.fullmatch(header)
    assert match is not None

    tree = ast.parse(source)
    values = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            values.append(ast.literal_eval(node.value))

    assert len(values) == 1
    assert isinstance(values[0], list)
    assert all(isinstance(item, str) for item in values[0])
    assert int(match.group("count")) == len(values[0])

    updated = datetime.fromisoformat(
        match.group("updated_at").replace("Z", "+00:00")
    )
    assert updated.tzinfo is not None
