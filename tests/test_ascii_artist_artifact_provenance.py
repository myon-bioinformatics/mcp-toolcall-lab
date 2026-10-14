import hashlib
import json
from pathlib import Path


def _locked(destination):
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))
    return next(e for e in lock["files"] if e["destination"] == destination)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "vendor" / "ascii_artist.py"
ARTIFACT_PROVENANCE = ROOT / "vendor" / "ascii_artist.provenance.json"

EXPECTED_ARTIFACT_SOURCE_COMMIT = _locked('vendor/ascii_artist.py')['commit']
EXPECTED_ARTIFACT_BLOB = _locked('vendor/ascii_artist.py')['blob_sha']
EXPECTED_ARTIFACT_SHA256 = _locked('vendor/ascii_artist.py')['sha256']


def _git_blob_sha1(data: bytes) -> str:
    payload = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    return hashlib.sha1(payload).hexdigest()


def test_vendored_ascii_artist_matches_recorded_provenance():
    record = json.loads(ARTIFACT_PROVENANCE.read_text(encoding="utf-8"))
    data = ARTIFACT.read_bytes()

    assert record["source_repository"] == "myon-bioinformatics/ascii_artist"
    assert record["source_path"] == "ascii_artist.py"
    assert record["source_commit"] == EXPECTED_ARTIFACT_SOURCE_COMMIT
    assert record["schema_version"] == "1.0"
    assert record["blob_sha"] == EXPECTED_ARTIFACT_BLOB
    assert record["sha256"] == EXPECTED_ARTIFACT_SHA256
    assert hashlib.sha256(data).hexdigest() == EXPECTED_ARTIFACT_SHA256
    assert _git_blob_sha1(data) == EXPECTED_ARTIFACT_BLOB
