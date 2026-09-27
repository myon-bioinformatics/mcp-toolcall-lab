"""Anonymous, read-only resolver for public GitHub resources.

The resolver distinguishes an observed HTTP result from an unverified claim.
It never accepts credentials and never turns an unobserved resource into
"inaccessible".
"""
from __future__ import annotations

from datetime import UTC, datetime
import http.client
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/-]+$")


def _repo(value: str) -> str:
    if not isinstance(value, str) or value.count("/") != 1:
        raise ValueError("repo must be owner/name")
    owner, name = value.split("/", 1)
    if not _OWNER.fullmatch(owner):
        raise ValueError("repo owner must use GitHub-safe alphanumeric/hyphen form")
    if not _NAME.fullmatch(name) or name in {".", ".."}:
        raise ValueError("repo name is invalid")
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
    pages = (
        f"https://{owner}.github.io/"
        if name.lower() == f"{owner.lower()}.github.io"
        else f"https://{owner}.github.io/{name}/"
    )
    out = [
        {"kind": "repository", "url": f"https://github.com/{repo}"},
        {"kind": "api", "url": f"https://api.github.com/repos/{repo}"},
        {"kind": "pages", "url": pages},
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


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
        if value is not None:
            return str(value)
    except (AttributeError, TypeError):
        pass
    try:
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value)
    except (AttributeError, TypeError):
        pass
    return None


def _rate_limited(headers: Any) -> bool:
    return (
        (_header(headers, "X-RateLimit-Remaining") or "").strip() == "0"
        or _header(headers, "Retry-After") is not None
    )


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
    except (URLError, TimeoutError, OSError, http.client.HTTPException):
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
