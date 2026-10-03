import hashlib
import importlib.util
import json
from pathlib import Path


def _locked(destination):
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))
    return next(e for e in lock["files"] if e["destination"] == destination)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "vendor" / "ascii_artist.py"
ARTIFACT_PROVENANCE = ROOT / "vendor" / "ascii_artist.provenance.json"
VALIDATOR = ROOT / "vendor" / "python_artifact_provenance.py"
VALIDATOR_PROVENANCE = ROOT / "vendor" / "python_artifact_provenance.provenance.json"

EXPECTED_ARTIFACT_SOURCE_COMMIT = _locked('vendor/ascii_artist.py')['commit']
EXPECTED_ARTIFACT_BLOB = _locked('vendor/ascii_artist.py')['blob_sha']
EXPECTED_ARTIFACT_SHA256 = _locked('vendor/ascii_artist.py')['sha256']
EXPECTED_ARTIFACT_BASE_SHA = "7c21bacfac7b60327b77f9b31a87869ef7838a7e"

EXPECTED_VALIDATOR_SOURCE_COMMIT = _locked('vendor/python_artifact_provenance.py')['commit']
EXPECTED_VALIDATOR_BLOB = _locked('vendor/python_artifact_provenance.py')['blob_sha']
EXPECTED_VALIDATOR_SHA256 = _locked('vendor/python_artifact_provenance.py')['sha256']


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "vendored_python_artifact_provenance",
        VALIDATOR,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load vendored provenance validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_blob_sha1(data: bytes) -> str:
    payload = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    return hashlib.sha1(payload).hexdigest()


VALIDATOR_MODULE = _load_validator()


def test_vendored_validator_matches_recorded_provenance():
    record = json.loads(VALIDATOR_PROVENANCE.read_text(encoding="utf-8"))
    data = VALIDATOR.read_bytes()

    assert record["source_repository"] == "myon-bioinformatics/Ironmate"
    assert record["source_path"] == "python_artifact_provenance.py"
    assert record["source_commit"] == EXPECTED_VALIDATOR_SOURCE_COMMIT
    assert record["schema_version"] == "1.0"
    assert record["blob_sha"] == EXPECTED_VALIDATOR_BLOB
    assert record["sha256"] == EXPECTED_VALIDATOR_SHA256
    assert hashlib.sha256(data).hexdigest() == EXPECTED_VALIDATOR_SHA256
    assert _git_blob_sha1(data) == EXPECTED_VALIDATOR_BLOB


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


def test_vendored_ascii_artist_passes_canonical_validator():
    metadata = VALIDATOR_MODULE.validate_source_header(
        ARTIFACT.read_text(encoding="utf-8")
    )
    assert metadata["all_count"] == 32
    assert metadata["base_sha"] == EXPECTED_ARTIFACT_BASE_SHA


def _assert_validator_rejects(source: str, expected: str) -> None:
    try:
        VALIDATOR_MODULE.validate_source_header(source)
    except ValueError as exc:
        assert expected in str(exc)
    else:
        raise AssertionError("expected canonical validator to reject mutated source")


def test_shared_validator_rejects_augmented_all_mutation():
    source = (
        f"# metadata: __all__=1 | base_sha={EXPECTED_ARTIFACT_BASE_SHA} | "
        "updated_at=2026-09-23T05:39:43Z\n"
        '__all__ = ["a"]\n'
        '__all__ += ["b"]\n'
        "def a(): pass\n"
        "def b(): pass\n"
    )
    _assert_validator_rejects(source, "literal top-level assignment")


def test_shared_validator_rejects_private_export():
    source = (
        f"# metadata: __all__=2 | base_sha={EXPECTED_ARTIFACT_BASE_SHA} | "
        "updated_at=2026-09-23T05:39:43Z\n"
        '__all__ = ["a", "_helper"]\n'
        "def a(): pass\n"
        "def _helper(): pass\n"
    )
    _assert_validator_rejects(source, "must not be exported")


def test_shared_validator_accepts_tuple_all():
    source = (
        f"# metadata: __all__=1 | base_sha={EXPECTED_ARTIFACT_BASE_SHA} | "
        "updated_at=2026-09-23T05:39:43Z\n"
        '__all__ = ("a",)\n'
        "def a(): pass\n"
    )
    metadata = VALIDATOR_MODULE.validate_source_header(source)
    assert metadata["all_count"] == 1
