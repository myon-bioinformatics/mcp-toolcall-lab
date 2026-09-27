import hashlib
import http.client
import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from mcp_toolcall_lab import repository_diagnostics as diagnostics
from mcp_toolcall_lab.github_public_resolver import candidate_urls, probe_url


class Response(BytesIO):
    def __init__(self, status=200, url="https://example.test/final", headers=None):
        super().__init__(b"")
        self.status = status
        self._url = url
        self.headers = headers or {}

    def getcode(self):
        return self.status

    def geturl(self):
        return self._url


def sample_record():
    contract = diagnostics._load_contract()
    return contract.build_repository_record(
        full_name="myon-bioinformatics/mcp-toolcall-lab",
        sha="168a284ff33fff88217e7f4252d50c6723a94ef9",
        branch="main",
        timestamp="2026-09-27T20:00:00+09:00",
        subject="feat: diagnostics",
        generated_at="2026-09-27T21:00:00+09:00",
        working_tree_bytes=1234,
        tooling={"python": "3.11"},
    )


def test_vendored_contract_provenance_matches_pin():
    provenance = json.loads(diagnostics.CONTRACT_PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert provenance["source_repository"] == "myon-bioinformatics/Ironmate"
    assert provenance["source_commit"] == "afdeb34026e7471dc01ece4eb9a75ca779bb207f"
    assert provenance["schema_version"] == "1.0"
    assert hashlib.sha256(diagnostics.CONTRACT_PATH.read_bytes()).hexdigest() == provenance["sha256"]


def test_payload_reuses_public_resolver_without_probe():
    payload = diagnostics.build_payload(sample_record(), probe=False)
    assert payload["metadata"]["schema_version"] == "1.0"
    assert payload["resolver"]["auth"] == "anonymous"
    assert payload["resolver"]["status"] == "not_checked"
    assert payload["resolver"]["observations"] == []


def test_pages_renderer_is_pinned_and_links_json_and_jsonl():
    html = diagnostics._renderer_page()
    assert diagnostics.WEB_UI_SHA in html
    assert "repository-diagnostics.json" in html
    assert "repository-diagnostics.jsonl" in html
    assert "RepositoryDiagnostics.render(payload.metadata)" in html
    assert "textContent = item.url" in html


def test_write_pages_outputs_json_jsonl_and_html(tmp_path, monkeypatch):
    record = sample_record()
    monkeypatch.setattr(diagnostics, "build_record", lambda env=None: record)
    payload = diagnostics.write_pages(tmp_path, probe=False)
    assert payload["metadata"] == record
    parsed = json.loads((tmp_path / diagnostics.JSON_NAME).read_text(encoding="utf-8"))
    assert parsed["metadata"] == record
    jsonl = json.loads((tmp_path / diagnostics.JSONL_NAME).read_text(encoding="utf-8"))
    assert jsonl == record
    assert (tmp_path / diagnostics.PAGE_NAME).is_file()


@pytest.mark.parametrize("repo", ["a.b/demo", "./demo", "bad-/demo", "octo/..", "octo/."])
def test_resolver_rejects_invalid_repository_identity(repo):
    with pytest.raises(ValueError):
        candidate_urls(repo)


def test_resolver_handles_user_pages_repository():
    rows = candidate_urls("octo/octo.github.io")
    pages = next(row["url"] for row in rows if row["kind"] == "pages")
    assert pages == "https://octo.github.io/"


def test_lowercase_rate_limit_header_and_retry_after_are_rate_limited():
    def lower_opener(request, timeout):
        raise HTTPError(request.full_url, 403, "limited", {"x-ratelimit-remaining": "0"}, None)

    assert probe_url("https://api.github.com/x", opener=lower_opener)["status"] == "rate_limited"

    def retry_opener(request, timeout):
        raise HTTPError(request.full_url, 403, "limited", {"retry-after": "60"}, None)

    assert probe_url("https://api.github.com/x", opener=retry_opener)["status"] == "rate_limited"


@pytest.mark.parametrize(
    "exc",
    [
        http.client.BadStatusLine("garbled"),
        http.client.IncompleteRead(b"partial", 10),
    ],
)
def test_http_protocol_failures_are_unverified(exc):
    def opener(request, timeout):
        raise exc

    result = probe_url("https://example.test/x", opener=opener)
    assert result["status"] == "unverified"
    assert result["http_status"] is None
    assert result["evidence"] == "network_error"
