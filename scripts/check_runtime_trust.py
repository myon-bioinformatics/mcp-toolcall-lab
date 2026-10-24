#!/usr/bin/env python3
"""Read-only TLS checks in the *running* Python/container, not on the build host.

The driver and offline tests use the standard library. Optional installed client
packages are imported only to exercise their own trust configuration. No package
is installed, no trust store is edited, and certificate verification stays on.
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit


SOURCES = {
    "fastmcp": "https://github.com/PrefectHQ/fastmcp/blob/main/SECURITY.md#certificate-authority-incident-october-2026",
    "incident": "https://emudhra.we-have-a-problem.com/",
    "hosts": "https://github.com/galadran/we-have-a-problem.com/blob/main/checker.js",
    "python": "https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576",
}
# Published incident targets, reviewed 2026-10-24. Never accept arbitrary hosts,
# credentials, query strings or redirects from CLI arguments or remote content.
INCIDENT_HOSTS = (
    "dv-g3b", "dv-g3c", "ov-g3b", "ov-g3c", "ev-g3b", "ev-g3c",
    "dv-g3b-aaa", "dv-g3c-aaa", "ov-g3b-aaa", "ov-g3c-aaa", "ev-g3b-aaa", "ev-g3c-aaa",
    "dv-g3b-noct", "dv-g3b-aaa-noct",
)
INCIDENT_MARKER = b"Your client successfully connected to the site."
MAX_BODY = 65536
USER_AGENT = "mcp-toolcall-lab-runtime-trust/1"
ENV_NAMES = (
    "SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE", "PYTHONHTTPSVERIFY",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT", "PIP_CLIENT_CERT",
    "PIP_TRUSTED_HOST", "PIP_USE_DEPRECATED", "UV_SYSTEM_CERTS", "UV_NATIVE_TLS",
    "UV_INSECURE_HOST", "UV_NO_VERIFY", "NODE_EXTRA_CA_CERTS",
    "NODE_TLS_REJECT_UNAUTHORIZED", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH", "GIT_SSL_NO_VERIFY",
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
    "https_proxy", "http_proxy", "all_proxy", "no_proxy",
)
UNTESTED_OVERRIDES = {
    "PIP_CERT", "PIP_CLIENT_CERT", "PIP_TRUSTED_HOST", "PIP_USE_DEPRECATED",
    "UV_INSECURE_HOST", "UV_NO_VERIFY", "PYTHONHTTPSVERIFY",
}
VERSION_FLOORS = {"certifi": "2026.10.19", "pip": "26.2.2", "uv": "0.13.4"}
DISTRIBUTIONS = (
    "pip", "uv", "certifi", "fastmcp", "fastmcp-slim", "mcp", "httpx2", "httpcore2",
    "httpx", "httpcore", "truststore",
)


class InconclusiveProbe(RuntimeError):
    """A known coverage gap, without including environment values in the error."""

    def __init__(self, reason: str):
        self.probe_reason = reason
        super().__init__(reason)


def targets() -> list[dict[str, str]]:
    return [
        {"name": "pypi", "kind": "normal", "url": "https://pypi.org/simple/pip/"},
        {"name": "python", "kind": "normal", "url": "https://www.python.org/"},
        {"name": "incident_liveness", "kind": "liveness", "url": "https://probe-emudhra.we-have-a-problem.com/"},
    ] + [
        {"name": host, "kind": "incident", "chain": "cross_signed" if "-aaa" in host else "direct",
         "url": f"https://{host}-emudhra.we-have-a-problem.com/"}
        for host in INCIDENT_HOSTS
    ]


def error_details(exc: BaseException) -> dict[str, Any]:
    """Do not serialize raw errors: proxy URLs and credentials can occur in them."""
    return {"error_type": f"{type(exc).__module__}.{type(exc).__name__}"}


def exception_chain(exc: BaseException) -> list[BaseException]:
    """Unwrap HTTPX, requests/urllib3 and urllib without trusting message text."""
    todo, seen, result = [exc], set(), []
    while todo:
        item = todo.pop()
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        result.append(item)
        todo.extend([item.__cause__, item.__context__, getattr(item, "reason", None)])
        todo.extend(arg for arg in item.args if isinstance(arg, BaseException))
        todo.extend(getattr(item, "exceptions", ()))
    return result


def classify_exception(exc: BaseException) -> dict[str, Any]:
    chain = exception_chain(exc)
    result = error_details(exc)
    if isinstance(exc, InconclusiveProbe):
        return result | {"outcome": "inconclusive", "reason": exc.probe_reason}
    # A timeout is inconclusive even if its text says CERTIFICATE_VERIFY_FAILED.
    if any(isinstance(e, (TimeoutError, subprocess.TimeoutExpired)) or
           "timeout" in type(e).__name__.lower() for e in chain):
        return result | {"outcome": "inconclusive", "reason": "timeout"}
    for item in chain:
        if isinstance(item, ssl.SSLCertVerificationError) or (
            isinstance(item, ssl.SSLError) and getattr(item, "reason", None) == "CERTIFICATE_VERIFY_FAILED"
        ):
            code = getattr(item, "verify_code", None)
            result |= {"tls_error": "CERTIFICATE_VERIFY_FAILED", "verify_code": code}
            # Expired/not-yet-valid leaves or hostname mismatch are not evidence
            # that a compromised CA was distrusted. Do not hide that distinction.
            if code in {9, 10, 62, 64}:
                return result | {"outcome": "inconclusive", "reason": "other_certificate_error"}
            return result | {"outcome": "certificate_rejected", "reason": "certificate_verification_failed"}
    if any(isinstance(e, socket.gaierror) for e in chain):
        reason = "dns_error"
    elif any(isinstance(e, ssl.SSLError) for e in chain):
        reason = "other_tls_error"
    else:
        reason = "transport_or_client_error"
    return result | {"outcome": "inconclusive", "reason": reason}


def env_metadata(environ: dict[str, str] | os._Environ[str] = os.environ) -> list[dict[str, Any]]:
    return [{"name": name, "nonempty": bool(environ[name]), "value": "[redacted]"}
            for name in ENV_NAMES if name in environ]


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def command_metadata(args: list[str]) -> dict[str, Any]:
    """Only call fixed, read-only version/package-query commands, never a shell."""
    if shutil.which(args[0]) is None:
        return {"status": "not_installed"}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        return {"status": "ok" if proc.returncode == 0 else "unavailable",
                "exit_code": proc.returncode, "output": proc.stdout.strip()[:8192]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable"} | error_details(exc)


def file_metadata(path: str | None, *, show_path: bool = True) -> dict[str, Any]:
    if not path:
        return {"exists": False}
    result: dict[str, Any] = {"path": path if show_path else "[environment override redacted]"}
    try:
        item = Path(path)
        result["exists"] = item.exists()
        if item.is_file():
            result["size"] = item.stat().st_size
            if show_path:
                result["sha256"] = hashlib.sha256(item.read_bytes()).hexdigest()
    except OSError as exc:
        result.update(error_details(exc))
    return result


def version_floor(name: str, version: str | None) -> dict[str, Any]:
    floor = VERSION_FLOORS[name]
    if version is None:
        status = "not_installed"
    elif not re.fullmatch(r"\d+(?:\.\d+)+", version):
        status = "unknown"
    else:
        actual = tuple(int(x) for x in version.split("."))
        required = tuple(int(x) for x in floor.split("."))
        status = "pass" if actual >= required else "below_floor"
    return {"installed": version, "minimum": floor, "status": status}


def runtime_metadata() -> dict[str, Any]:
    paths = ssl.get_default_verify_paths()
    versions = {name: package_version(name) for name in DISTRIBUTIONS}
    uv = command_metadata(["uv", "--version"])
    # uv is often a standalone executable rather than a Python distribution.
    if uv["status"] == "ok":
        match = re.match(r"uv (\d+(?:\.\d+)+)(?:\s|$)", uv["output"])
        if match:
            versions["uv"] = match.group(1)
        else:
            versions["uv"] = "unrecognized_executable_version"
    elif uv["status"] == "unavailable":
        versions["uv"] = "unavailable_executable_version"
    try:
        os_release = platform.freedesktop_os_release()
    except OSError:
        os_release = {}
    return {
        "python": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                   "executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix},
        "openssl": ssl.OPENSSL_VERSION,
        "os": {key: os_release.get(key) for key in ("ID", "VERSION_ID", "PRETTY_NAME")},
        "architecture": platform.machine(),
        "container_marker_present": Path("/.dockerenv").exists() or Path("/run/.containerenv").exists(),
        "distributions": versions,
        "uv_executable": uv,
        "security_version_floors": {name: version_floor(name, versions[name]) for name in VERSION_FLOORS},
        "os_trust_packages": {
            "dpkg": command_metadata(["dpkg-query", "-W", "-f=${Package}\t${Version}\n", "ca-certificates", "openssl", "libssl3*", "libssl4*"]),
            "apk": command_metadata(["apk", "info", "-v", "ca-certificates", "ca-certificates-bundle", "openssl", "libssl3"]),
            "rpm": command_metadata(["rpm", "-q", "ca-certificates", "openssl", "openssl-libs"]),
        },
        "system_trust": {
            "cafile": file_metadata(paths.cafile, show_path="SSL_CERT_FILE" not in os.environ),
            "capath": file_metadata(paths.capath, show_path="SSL_CERT_DIR" not in os.environ),
            "compiled_cafile": paths.openssl_cafile,
            "compiled_capath": paths.openssl_capath,
        },
        "environment_overrides": env_metadata(),
    }


def context_metadata(context: ssl.SSLContext) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": f"{type(context).__module__}.{type(context).__name__}",
        "verify_mode": int(context.verify_mode), "check_hostname": context.check_hostname,
    }
    try:
        result["certificate_store_stats"] = context.cert_store_stats()
    except (AttributeError, NotImplementedError):
        result["certificate_store_stats"] = "not_exposed_by_client"
    return result


def require_verification(context: ssl.SSLContext) -> None:
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ValueError("Client does not enforce certificate and hostname verification")


@contextmanager
def deadline(seconds: float) -> Iterator[None]:
    """Linux-container wall clock bound includes DNS, not just socket I/O."""
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    def expired(signum: int, frame: Any) -> None:
        raise TimeoutError("Probe deadline exceeded")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def ssl_request(context: ssl.SSLContext, url: str, timeout: float) -> dict[str, Any]:
    """Direct HTTPS, with no proxy, credentials, redirects or downloaded code."""
    require_verification(context)
    parsed = urlsplit(url)
    conn = http.client.HTTPSConnection(parsed.hostname, 443, timeout=timeout, context=context)
    try:
        conn.request("GET", parsed.path or "/", headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
        response = conn.getresponse()
        body = response.read(MAX_BODY)
        return {"http_status": response.status, "incident_marker": INCIDENT_MARKER in body}
    finally:
        conn.close()


def client_context(client: Any, url: str | None = None) -> ssl.SSLContext:
    # These attributes are deliberately checked: an upstream transport change
    # must yield an incomplete receipt, not a fabricated trust-store claim.
    transport = client._transport if url is None else client._transport_for_url(client.build_request("GET", url).url)
    pool = transport._pool
    if "proxy" in type(pool).__name__.lower():
        # Neither a proxy block page nor rejection of its own TLS certificate
        # demonstrates distrust of the incident's origin chain. Never send
        # configured proxy credentials as a side effect of these checks.
        raise InconclusiveProbe("proxy_route_not_tested")
    context = pool._ssl_context
    require_verification(context)
    return context


async def async_client_request(factory: Callable[[], Any], url: str, timeout: float) -> dict[str, Any]:
    async with factory() as client:
        evidence = {"client_type": f"{type(client).__module__}.{type(client).__name__}",
                    "ssl_context": context_metadata(client_context(client, url))}
        async with client.stream("GET", url, follow_redirects=False, auth=None,
                                 headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk[:MAX_BODY - len(body)])
                if len(body) >= MAX_BODY:
                    break
            return evidence | {"http_status": response.status_code, "incident_marker": INCIDENT_MARKER in body}


def discover_backends() -> list[dict[str, Any]]:
    backends: list[dict[str, Any]] = [{"name": "system_ssl", "status": "available",
                                     "routing": "direct_no_environment_proxy", "scope": "ssl.create_default_context()"}]
    for name, module_name in (("certifi_bundle", "certifi"), ("pip_vendored_bundle", "pip._vendor.certifi")):
        backend: dict[str, Any] = {"name": name, "routing": "direct_no_environment_proxy",
                                  "scope": "Explicit bundle in this Python SSL runtime; not the package manager itself"}
        try:
            module = importlib.import_module(module_name)
            backend |= {"status": "available", "bundle": file_metadata(module.where()),
                        "bundle_version": getattr(module, "__version__", None)}
        except ModuleNotFoundError as exc:
            backend |= {"status": "not_installed" if exc.name in {"pip", "certifi"} else "unavailable"} | error_details(exc)
        except Exception as exc:
            backend |= {"status": "unavailable"} | error_details(exc)
        backends.append(backend)
    for name in ("httpx", "httpx2"):
        if package_version(name) is not None:
            backends.append({"name": name, "status": "available", "scope": f"{name}.AsyncClient default TLS",
                             "routing": "client_default_environment_proxy_handling"})
    if package_version("pip") is not None:
        backends.append({"name": "pip_default_session", "status": "available",
                         "routing": "client_default_environment_proxy_handling",
                         "scope": "PipSession plus pip's default truststore factory; no pip config files, credentials or trusted-host bypass"})
    fastmcp = {"name": "fastmcp_default_client", "status": "not_installed",
               "routing": "client_default_environment_proxy_handling",
               "scope": "Installed FastMCP Streamable HTTP default factory; no application-specific custom factory"}
    if package_version("fastmcp") is not None or package_version("fastmcp-slim") is not None:
        try:
            transport = importlib.import_module("fastmcp.client.transports.http")
            factory = transport.create_mcp_http_client
            fastmcp |= {"status": "available", "factory": f"{factory.__module__}.{factory.__name__}",
                        "transport_module": transport.__name__, "http_library": transport.httpx2.__name__}
        except Exception as exc:
            fastmcp |= {"status": "unavailable"} | error_details(exc)
    backends.append(fastmcp)
    return backends


def perform_request(backend: dict[str, Any], url: str, timeout: float) -> dict[str, Any]:
    name = backend["name"]
    if name in {"system_ssl", "certifi_bundle", "pip_vendored_bundle"}:
        if name == "system_ssl":
            context = ssl.create_default_context()
        else:
            context = ssl.create_default_context(cafile=backend["bundle"]["path"])
        return ssl_request(context, url, timeout) | {"ssl_context": context_metadata(context)}
    if name in {"httpx", "httpx2", "fastmcp_default_client"}:
        if name == "fastmcp_default_client":
            transport = importlib.import_module("fastmcp.client.transports.http")
            factory = lambda: transport.create_mcp_http_client(timeout=transport.httpx2.Timeout(timeout))
        else:
            module = importlib.import_module(name)
            factory = lambda: module.AsyncClient(timeout=timeout)
        return asyncio.run(async_client_request(factory, url, timeout))
    if name == "pip_default_session":
        from pip._internal.cli.index_command import _create_truststore_ssl_context
        from pip._internal.network.session import PipSession
        from pip._vendor.requests.utils import select_proxy
        context = _create_truststore_ssl_context()
        if context is None:
            raise RuntimeError("Pip default truststore context is unavailable")
        require_verification(context)
        with PipSession(retries=0, ssl_context=context, index_urls=[]) as client:
            # Do not perform keyring/netrc discovery or send account credentials.
            client.auth = lambda request: request
            settings = client.merge_environment_settings(url, {}, None, None, None)
            if select_proxy(url, settings["proxies"]):
                raise InconclusiveProbe("proxy_route_not_tested")
            with client.get(url, timeout=timeout, allow_redirects=False, stream=True,
                            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}) as response:
                body = response.raw.read(MAX_BODY)
                return {"http_status": response.status_code, "incident_marker": INCIDENT_MARKER in body,
                        "client_type": f"{type(client).__module__}.{type(client).__name__}",
                        "ssl_context": context_metadata(context)}
    raise ValueError("Unknown backend")


def run_probe(backend: dict[str, Any], target: dict[str, str], timeout: float,
              request: Callable[..., dict[str, Any]] = perform_request) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = dict(target)
    try:
        with deadline(timeout):
            response = request(backend, target["url"], timeout)
        result.update(response)
        if target["kind"] in {"incident", "liveness"} and not response.get("incident_marker"):
            result |= {"outcome": "inconclusive", "reason": "unexpected_response_possible_interception"}
        elif not 200 <= response["http_status"] < 300:
            result |= {"outcome": "inconclusive", "reason": "unexpected_http_status"}
        else:
            result |= {"outcome": "accepted", "reason": "verified_https_response"}
    except Exception as exc:
        result.update(classify_exception(exc))
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["passed"] = result["outcome"] == ("certificate_rejected" if target["kind"] == "incident" else "accepted")
    return result


def run_backend(backend: dict[str, Any], timeout: float,
                request: Callable[..., dict[str, Any]] = perform_request) -> dict[str, Any]:
    result = dict(backend)
    if backend["status"] != "available":
        result["probes"] = []
        return result
    positive = [target for target in targets() if target["kind"] != "incident"]
    negative = [target for target in targets() if target["kind"] == "incident"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        probes = list(pool.map(lambda target: run_probe(backend, target, timeout, request), positive))
        if all(probe["passed"] for probe in probes):
            probes.extend(pool.map(lambda target: run_probe(backend, target, timeout, request), negative))
        else:
            probes.extend(target | {"outcome": "not_run", "reason": "positive_control_failed", "passed": False}
                          for target in negative)
    result["probes"] = probes
    result["status"] = "checked"
    return result


def run_backend_subprocess(backend: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Hard deadline survives stuck DNS/extension calls and thread I/O timers."""
    if backend["status"] != "available":
        return dict(backend) | {"probes": []}
    # Three controls in one wave, fourteen incident targets in four waves, plus
    # a bounded allowance for interpreter and optional-package initialization.
    wall_timeout = timeout * 5 + 20
    try:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--live", "--timeout", str(timeout),
             "--_backend", backend["name"]],
            capture_output=True, text=True, timeout=wall_timeout, check=False,
        )
        result = json.loads(proc.stdout)
        if proc.returncode != 0 or not isinstance(result, dict) or result.get("name") != backend["name"]:
            raise ValueError("Invalid backend subprocess receipt")
        return result
    except Exception as exc:
        failure = classify_exception(exc)
        return dict(backend) | {"status": "unavailable", "worker": failure,
                               "probes": [target | failure | {"passed": False} for target in targets()]}


def summarize(receipt: dict[str, Any], require_fastmcp: bool = False) -> tuple[str, int]:
    if receipt["mode"] == "metadata_only":
        return "not_checked", 0
    if receipt.get("blocked_reason"):
        return "inconclusive", 2
    backends = receipt["backends"]
    if any(probe["kind"] == "incident" and probe["outcome"] == "accepted"
           for backend in backends for probe in backend.get("probes", [])):
        return "exposed", 1
    floors = receipt["runtime"]["security_version_floors"].values()
    if any(floor["status"] == "below_floor" for floor in floors):
        return "outdated", 1
    if any(floor["status"] == "unknown" for floor in receipt["runtime"]["security_version_floors"].values()):
        return "inconclusive", 2
    if receipt.get("untested_override_names"):
        return "inconclusive", 2
    if require_fastmcp and not any(b["name"] == "fastmcp_default_client" and b["status"] == "checked" for b in backends):
        return "inconclusive", 2
    for backend in backends:
        if backend["status"] == "not_installed":
            continue
        if backend["status"] != "checked" or len(backend["probes"]) != len(targets()) or not all(p["passed"] for p in backend["probes"]):
            return "inconclusive", 2
    if not any(b["name"] == "system_ssl" and b["status"] == "checked" for b in backends):
        return "inconclusive", 2
    return "pass", 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--metadata-only", action="store_true", help="Inventory only: exit 0 does NOT mean trust passed")
    mode.add_argument("--live", action="store_true", help="Read-only HTTPS probes of fixed public incident/control sites")
    parser.add_argument("--output", type=Path, help="Also write the complete JSON receipt to this path")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request I/O timeout in seconds; backend also has a hard deadline (default 10)")
    parser.add_argument("--require-fastmcp", action="store_true", help="Fail if the installed FastMCP default HTTP client cannot be tested")
    parser.add_argument("--_backend", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 120:
        parser.error("--timeout must be greater than zero and at most 120 seconds")
    sys.dont_write_bytecode = True
    if args._backend:
        if os.environ.get("SSLKEYLOGFILE"):
            parser.error("SSLKEYLOGFILE is set; refusing to create live TLS clients")
        selected = [backend for backend in discover_backends() if backend["name"] == args._backend]
        if len(selected) != 1:
            parser.error("Unknown backend")
        sys.stdout.write(json.dumps(run_backend(selected[0], args.timeout)) + "\n")
        return 0
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if args.live else "metadata_only", "sources": SOURCES,
        "runtime": runtime_metadata(), "backends": [], "require_fastmcp": args.require_fastmcp,
        "limits": [
            "Only this Python process and explicitly listed default clients are tested; restart and test actual deployed containers separately.",
            "uv is inventoried and version-gated, not a live uv transport probe. Custom application TLS contexts and pip configuration files are not exercised.",
            "Bundle checks use this Python/OpenSSL verifier; a runtime-level block does not prove a root was physically removed from a bundle.",
            "Proxy settings are reported by name only. Direct SSL probes do not use proxies; an HTTP client's selected proxy route is inconclusive and not contacted.",
            "No verification bypass, package installation, trust-store edit, client credentials, redirects or user-host probes are performed.",
        ],
    }
    receipt["untested_override_names"] = sorted(name for name in UNTESTED_OVERRIDES if os.environ.get(name))
    if os.environ.get("SSLKEYLOGFILE"):
        # Creating an SSLContext can itself open a key-log file. Do not do that.
        receipt["blocked_reason"] = "SSLKEYLOGFILE is set; live clients were not created to avoid writing TLS secrets"
    else:
        receipt["backends"] = discover_backends()
        if args.live:
            with ThreadPoolExecutor(max_workers=2) as pool:
                receipt["backends"] = list(pool.map(lambda backend: run_backend_subprocess(backend, args.timeout), receipt["backends"]))
    receipt["status"], exit_code = summarize(receipt, args.require_fastmcp)
    receipt["exit_code"] = exit_code
    payload = json.dumps(receipt, ensure_ascii=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    sys.stdout.write(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
