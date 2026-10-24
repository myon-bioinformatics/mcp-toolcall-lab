"""Offline trust-receipt regression tests; no network or third-party dependencies.

Run directly with: python -m unittest tests.test_runtime_trust
They are also collected by the project's normal pytest lane.
"""

from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_runtime_trust.py"
SPEC = importlib.util.spec_from_file_location("check_runtime_trust", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
trust = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trust)


def certificate_error(code: int = 20) -> ssl.SSLCertVerificationError:
    exc = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] verification failed")
    exc.verify_code = code
    return exc


def backend() -> dict:
    return {"name": "system_ssl", "status": "available"}


def good_request(selected: dict, url: str, timeout: float) -> dict:
    if url in {t["url"] for t in trust.targets() if t["kind"] == "incident"}:
        raise certificate_error()
    return {"http_status": 200, "incident_marker": True}


def good_receipt() -> dict:
    return {"mode": "live", "runtime": {"security_version_floors": {
        name: trust.version_floor(name, version) for name, version in trust.VERSION_FLOORS.items()
    }}, "backends": [trust.run_backend(backend(), 1, good_request)], "untested_override_names": []}


class ExceptionClassificationTests(unittest.TestCase):
    def test_real_certificate_rejection(self) -> None:
        result = trust.classify_exception(certificate_error())
        self.assertEqual(result["outcome"], "certificate_rejected")
        self.assertEqual(result["tls_error"], "CERTIFICATE_VERIFY_FAILED")
        self.assertEqual(result["verify_code"], 20)

    def test_nested_httpx_cause(self) -> None:
        wrapped = RuntimeError("request failed")
        wrapped.__cause__ = certificate_error()
        self.assertEqual(trust.classify_exception(wrapped)["outcome"], "certificate_rejected")

    def test_requests_args_and_urlopen_reason(self) -> None:
        for wrapped in (RuntimeError(certificate_error()), URLError(certificate_error())):
            with self.subTest(wrapper=type(wrapped).__name__):
                self.assertEqual(trust.classify_exception(wrapped)["outcome"], "certificate_rejected")

    def test_timeout_is_never_distrust_even_with_misleading_text(self) -> None:
        for wrapped in (TimeoutError("CERTIFICATE_VERIFY_FAILED"), URLError(TimeoutError("SSL failed")),
                        subprocess.TimeoutExpired(["python"], 1)):
            result = trust.classify_exception(wrapped)
            self.assertEqual(result["outcome"], "inconclusive")
            self.assertEqual(result["reason"], "timeout")
            self.assertNotIn("tls_error", result)

    def test_httpx_timeout_class_without_timeout_base(self) -> None:
        class ConnectTimeout(Exception):
            pass
        self.assertEqual(trust.classify_exception(ConnectTimeout())["reason"], "timeout")

    def test_untyped_error_text_is_not_tls_evidence(self) -> None:
        result = trust.classify_exception(RuntimeError("CERTIFICATE_VERIFY_FAILED"))
        self.assertEqual(result["outcome"], "inconclusive")

    def test_dns_failure_is_not_tls_evidence(self) -> None:
        self.assertEqual(trust.classify_exception(socket.gaierror(-2, "not found"))["reason"], "dns_error")

    def test_generic_ssl_error_is_not_certificate_rejection(self) -> None:
        self.assertEqual(trust.classify_exception(ssl.SSLError(1, "wrong protocol"))["outcome"], "inconclusive")

    def test_expiry_and_hostname_failures_are_not_ca_distrust(self) -> None:
        for code in (9, 10, 62, 64):
            result = trust.classify_exception(certificate_error(code))
            self.assertEqual(result["outcome"], "inconclusive")
            self.assertEqual(result["reason"], "other_certificate_error")

    def test_exception_cycles_do_not_loop(self) -> None:
        first, second = RuntimeError(), certificate_error()
        first.__cause__, second.__cause__ = second, first
        self.assertEqual(len(trust.exception_chain(first)), 2)

    def test_errors_do_not_leak_proxy_credentials(self) -> None:
        secret = "https://user:SUPER_SECRET@proxy.example"
        result = trust.classify_exception(OSError(secret))
        self.assertNotIn("SUPER_SECRET", json.dumps(result))
        self.assertNotIn("proxy.example", json.dumps(result))


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.incident = next(t for t in trust.targets() if t["kind"] == "incident")
        self.normal = trust.targets()[0]

    def test_fixed_targets_cover_both_chains_and_no_ct(self) -> None:
        targets = trust.targets()
        incidents = [t for t in targets if t["kind"] == "incident"]
        self.assertEqual(len(incidents), 14)
        self.assertEqual({t["chain"] for t in incidents}, {"direct", "cross_signed"})
        self.assertEqual(sum("noct" in t["name"] for t in incidents), 2)
        self.assertEqual(len({t["url"] for t in targets}), 17)
        for target in targets:
            self.assertTrue(target["url"].startswith("https://"))
            self.assertNotIn("@", target["url"])

    def test_negative_control_must_fail_certificate_verification(self) -> None:
        result = trust.run_probe(backend(), self.incident, 1, Mock(side_effect=certificate_error()))
        self.assertTrue(result["passed"])

    def test_negative_control_timeout_is_inconclusive(self) -> None:
        result = trust.run_probe(backend(), self.incident, 1, Mock(side_effect=TimeoutError()))
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "timeout")

    def test_positive_control_certificate_failure_fails(self) -> None:
        result = trust.run_probe(backend(), self.normal, 1, Mock(side_effect=certificate_error()))
        self.assertFalse(result["passed"])

    def test_reachable_incident_is_exposed(self) -> None:
        result = trust.run_probe(backend(), self.incident, 1,
                                 Mock(return_value={"http_status": 200, "incident_marker": True}))
        self.assertEqual(result["outcome"], "accepted")
        self.assertFalse(result["passed"])

    def test_proxy_block_page_not_called_acceptance_or_distrust(self) -> None:
        result = trust.run_probe(backend(), self.incident, 1,
                                 Mock(return_value={"http_status": 200, "incident_marker": False}))
        self.assertEqual(result["outcome"], "inconclusive")

    def test_redirect_not_positive_control_success(self) -> None:
        result = trust.run_probe(backend(), self.normal, 1,
                                 Mock(return_value={"http_status": 302, "incident_marker": False}))
        self.assertFalse(result["passed"])

    def test_failed_liveness_preserves_every_host_as_not_run(self) -> None:
        request = Mock(side_effect=TimeoutError())
        result = trust.run_backend(backend(), 1, request)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(len(result["probes"]), 17)
        self.assertTrue(all(p["reason"] == "positive_control_failed" for p in result["probes"][3:]))

    def test_passing_backend_tests_every_host(self) -> None:
        result = trust.run_backend(backend(), 1, good_request)
        self.assertEqual(len(result["probes"]), 17)
        self.assertTrue(all(p["passed"] for p in result["probes"]))

    def test_unsafe_context_is_never_used(self) -> None:
        context = Mock(verify_mode=ssl.CERT_NONE, check_hostname=False)
        with patch.object(trust.http.client, "HTTPSConnection") as connect:
            with self.assertRaises(ValueError):
                trust.ssl_request(context, self.normal["url"], 1)
            connect.assert_not_called()

    def test_worker_deadline_produces_exact_inconclusive_receipts(self) -> None:
        with patch.object(trust.subprocess, "run", side_effect=subprocess.TimeoutExpired(["python"], 25)):
            result = trust.run_backend_subprocess(backend(), 1)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(len(result["probes"]), 17)
        self.assertTrue(all(p["outcome"] == "inconclusive" and p["reason"] == "timeout" for p in result["probes"]))

    def test_worker_malformed_output_does_not_pass(self) -> None:
        proc = SimpleNamespace(returncode=0, stdout="not json", stderr="SECRET")
        with patch.object(trust.subprocess, "run", return_value=proc):
            result = trust.run_backend_subprocess(backend(), 1)
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("SECRET", json.dumps(result))


class SummaryTests(unittest.TestCase):
    def test_stdlib_image_passes_without_fastmcp(self) -> None:
        receipt = good_receipt()
        receipt["backends"].append({"name": "fastmcp_default_client", "status": "not_installed", "probes": []})
        self.assertEqual(trust.summarize(receipt), ("pass", 0))
        self.assertEqual(trust.summarize(receipt, require_fastmcp=True), ("inconclusive", 2))

    def test_missing_optional_dependencies_are_not_failures(self) -> None:
        receipt = good_receipt()
        for name in trust.VERSION_FLOORS:
            receipt["runtime"]["security_version_floors"][name] = trust.version_floor(name, None)
        receipt["backends"].append({"name": "certifi_bundle", "status": "not_installed", "probes": []})
        self.assertEqual(trust.summarize(receipt), ("pass", 0))

    def test_exposed_chain_nonzero(self) -> None:
        receipt = good_receipt()
        receipt["backends"][0]["probes"][3] |= {"outcome": "accepted", "passed": False}
        self.assertEqual(trust.summarize(receipt), ("exposed", 1))

    def test_timeout_nonzero(self) -> None:
        receipt = good_receipt()
        receipt["backends"][0]["probes"][3] |= {"outcome": "inconclusive", "reason": "timeout", "passed": False}
        self.assertEqual(trust.summarize(receipt), ("inconclusive", 2))

    def test_any_positive_control_failure_nonzero(self) -> None:
        receipt = good_receipt()
        receipt["backends"][0]["probes"][0]["passed"] = False
        self.assertEqual(trust.summarize(receipt), ("inconclusive", 2))

    def test_no_system_probe_is_not_a_pass(self) -> None:
        receipt = good_receipt()
        receipt["backends"] = []
        self.assertEqual(trust.summarize(receipt), ("inconclusive", 2))

    def test_metadata_is_explicitly_not_checked(self) -> None:
        receipt = good_receipt() | {"mode": "metadata_only"}
        self.assertEqual(trust.summarize(receipt), ("not_checked", 0))

    def test_missing_host_receipt_does_not_pass(self) -> None:
        receipt = good_receipt()
        receipt["backends"][0]["probes"].pop()
        self.assertEqual(trust.summarize(receipt), ("inconclusive", 2))

    def test_old_package_floor_fails_even_with_runtime_defense(self) -> None:
        receipt = good_receipt()
        receipt["runtime"]["security_version_floors"]["pip"] = trust.version_floor("pip", "26.2.1")
        self.assertEqual(trust.summarize(receipt), ("outdated", 1))

    def test_prerelease_or_unknown_package_floor_not_claimed_pass(self) -> None:
        for version in ("26.2.2rc1", "26.2.2+vendor", "unknown"):
            self.assertEqual(trust.version_floor("pip", version)["status"], "unknown")

    def test_untested_override_prevents_full_pass(self) -> None:
        receipt = good_receipt() | {"untested_override_names": ["PIP_CERT"]}
        self.assertEqual(trust.summarize(receipt), ("inconclusive", 2))


class MetadataAndClientTests(unittest.TestCase):
    def test_env_values_always_redacted(self) -> None:
        result = trust.env_metadata({"SSL_CERT_FILE": "/SECRET.pem", "HTTPS_PROXY": "https://secret@proxy", "UNRELATED": "x"})
        self.assertEqual(len(result), 2)
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("secret@proxy", json.dumps(result))
        self.assertNotIn("UNRELATED", json.dumps(result))

    def test_default_ssl_path_override_not_leaked(self) -> None:
        with tempfile.NamedTemporaryFile() as file:
            data = trust.file_metadata(file.name, show_path=False)
        self.assertNotIn(file.name, json.dumps(data))
        self.assertNotIn("sha256", data)

    def test_metadata_mode_writes_json_without_network(self) -> None:
        runtime = good_receipt()["runtime"]
        with tempfile.TemporaryDirectory() as temp, patch.object(trust, "runtime_metadata", return_value=runtime), \
             patch.object(trust, "discover_backends", return_value=[]), \
             patch.object(trust, "run_backend_subprocess") as run, \
             patch.dict(trust.os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as stdout:
            output = Path(temp) / "receipt.json"
            code = trust.main(["--metadata-only", "--output", str(output)])
            saved = json.loads(output.read_text())
            self.assertEqual(saved, json.loads(stdout.getvalue()))
            self.assertEqual(saved["status"], "not_checked")
            self.assertEqual(code, 0)
            run.assert_not_called()

    def test_key_logging_setting_blocks_context_creation(self) -> None:
        with patch.object(trust, "runtime_metadata", return_value=good_receipt()["runtime"]), \
             patch.object(trust, "discover_backends") as discover, \
             patch.dict(trust.os.environ, {"SSLKEYLOGFILE": "/SECRET.log"}, clear=True), \
             redirect_stdout(io.StringIO()) as stdout:
            code = trust.main(["--live"])
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "inconclusive")
        self.assertNotIn("/SECRET.log", stdout.getvalue())
        discover.assert_not_called()

    def test_actual_fastmcp_factory_and_timeout_are_used(self) -> None:
        client = object()
        module = SimpleNamespace(create_mcp_http_client=Mock(return_value=client), httpx2=SimpleNamespace(Timeout=Mock(return_value="timeout")))
        async def probe(factory, url, timeout):
            self.assertIs(factory(), client)
            return {"http_status": 200}
        with patch.object(trust.importlib, "import_module", return_value=module) as load, \
             patch.object(trust, "async_client_request", side_effect=probe):
            result = trust.perform_request({"name": "fastmcp_default_client"}, "https://www.python.org/", 3)
        load.assert_called_once_with("fastmcp.client.transports.http")
        module.create_mcp_http_client.assert_called_once_with(timeout="timeout")
        module.httpx2.Timeout.assert_called_once_with(3)
        self.assertEqual(result["http_status"], 200)

    def test_actual_client_context_must_be_inspectable(self) -> None:
        with self.assertRaises(AttributeError):
            trust.client_context(SimpleNamespace())

    def test_selected_proxy_mount_not_default_transport_is_inspected(self) -> None:
        class AsyncHTTPProxy:
            pass
        context = SimpleNamespace(verify_mode=ssl.CERT_REQUIRED, check_hostname=True)
        client = SimpleNamespace(
            _transport=SimpleNamespace(_pool=SimpleNamespace(_ssl_context=context)),
            build_request=Mock(return_value=SimpleNamespace(url="selected-url")),
            _transport_for_url=Mock(return_value=SimpleNamespace(_pool=AsyncHTTPProxy())),
        )
        with self.assertRaises(trust.InconclusiveProbe) as caught:
            trust.client_context(client, "https://www.python.org/")
        self.assertEqual(trust.classify_exception(caught.exception)["reason"], "proxy_route_not_tested")
        client._transport_for_url.assert_called_once_with("selected-url")


if __name__ == "__main__":
    unittest.main()
