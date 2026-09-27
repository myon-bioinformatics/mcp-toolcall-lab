"""Anonymous, read-only resolver for public GitHub resources.

The resolver distinguishes an observed HTTP result from an unverified claim.
It never accepts credentials and never turns an unobserved resource into
"inaccessible".
"""
from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/-]+$")


def _repo(value: str) -> str:
    if not isinstance(value, str) or not _REPO.fullmatch(value):
        raise ValueError("repo must be owner/name")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ValueError("repo owner/name cannot be dot segments")
    return value


def _ref(value: str) -> str:
    if not isinstance(value, str) or not value or not _REF.fullmatch(value) or ".." in value:
        raise ValueError("ref must be a simple Git ref")
    return value


def candidate_urls(repo: str, *, ref: str = "main", path: str | None = None) -> list[dict[str, str]]:
    """Build public GitHub UI/API/raw/Pages candidates without network access."""
    repo = _repo(repo)
    ref = _ref(ref)
    owner, name = repo.split("/", 1)
    out = [
        {"kind": "repository", "url": f"https://github.com/{repo}"},
        {"kind": "api", "url": f"https://api.github.com/repos/{repo}"},
        {"kind": "pages", "url": f"https://{owner}.github.io/{name}/"},
    ]
    if path is not None:
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("path must be a relative repository path")
        encoded = "/".join(quote(part, safe="") for part in path.split("/"))
        out.extend([
            {"kind": "raw", "url": f"https://raw.githubusercontent.com/{repo}/{quote(ref, safe='/')}/{encoded}"},
            {"kind": "contents_api", "url": f"https://api.github.com/repos/{repo}/contents/{encoded}?ref={quote(ref, safe='')}"},
        ])
    return out


def _rate_limited(headers: Any) -> bool:
    if headers is None:
        return False
    try:
        return str(headers.get("X-RateLimit-Remaining", "")).strip() == "0"
    except (AttributeError, TypeError):
        return False


def _status(code: int, headers: Any = None) -> str:
    if 200 <= code < 400:
        return "reachable"
    if code == 403 and _rate_limited(headers):
        return "rate_limited"
    if code in (401, 403):
        return "auth_required"
    if code == 404:
        return "not_found"
    if code == 429:
        return "rate_limited"
    return "http_error"


def probe_url(
    url: str,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: float = 10,
) -> dict[str, Any]:
    """Observe one public HTTPS URL. No token/cookie is accepted or attached."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("url must use https")
    checked_at = datetime.now(UTC).isoformat()
    request = Request(url, headers={"User-Agent": "mcp-toolcall-lab-public-resolver/1"})
    try:
        with opener(request, timeout=timeout) as response:
            code = int(getattr(response, "status", response.getcode()))
            final_url = response.geturl()
            headers = getattr(response, "headers", None)
    except HTTPError as exc:
        code = exc.code
        final_url = exc.geturl()
        headers = exc.headers
    except (URLError, TimeoutError, OSError):
        return {
            "status": "unverified",
            "url": url,
            "resolved_url": None,
            "http_status": None,
            "checked_at": checked_at,
            "evidence": "network_error",
        }
    return {
        "status": _status(code, headers),
        "url": url,
        "resolved_url": final_url,
        "http_status": code,
        "checked_at": checked_at,
        "evidence": "http_response",
    }


def resolve_public_github(
    repo: str,
    *,
    ref: str = "main",
    path: str | None = None,
    probe: bool = True,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Return candidates and, when requested, direct anonymous HTTP evidence."""
    candidates = candidate_urls(repo, ref=ref, path=path)
    observations = [
        {**item, **probe_url(item["url"], opener=opener)}
        for item in candidates
    ] if probe else []
    return {
        "repository": repo,
        "ref": ref,
        "path": path,
        "auth": "anonymous",
        "candidates": candidates,
        "observations": observations,
        "status": "observed" if probe else "not_checked",
    }
