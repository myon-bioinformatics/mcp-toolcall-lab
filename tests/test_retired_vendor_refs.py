"""Retired upstream source paths must not follow a branch that deleted them."""
import json
from pathlib import Path


def test_ironmate_sources_keep_immutable_refs_and_recorded_bytes():
    import hashlib
    root = Path(__file__).resolve().parents[1]
    entries = [e for e in json.loads((root / 'vendor.lock.json').read_text())['files']
               if e['repository'] == 'myon-bioinformatics/Ironmate']
    assert {e['source'] for e in entries} == {'LICENSE'}
    for e in entries:
        assert e['ref'] == e['commit']
        data = (root / e['destination']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == e['sha256']
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == e['blob_sha']
