"""Offline identities for observed chat/agent URLs; never fetch conversation text."""
from __future__ import annotations

import argparse
import json
import re
from urllib.parse import urlsplit

# Observed routes, not a promise that the service exposes a content API.
_ROUTES = {
    "chatgpt.com": ("chatgpt", "c", "conversation_id"),
    "gemini.google.com": ("gemini", "app", "conversation_id"),
    "claude.ai": ("claude", "chat", "conversation_id"),
    "m365.cloud.microsoft": ("m365_copilot", "chat/conversation", "conversation_id"),
    "cursor.com": ("cursor", "agents", "agent_id"),
    "github.com": ("github_copilot", "copilot/c", "conversation_id"),
}
KNOWN_HOSTS = frozenset(_ROUTES)
_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}"
_RESERVED = {"new", "login", "register", "auth"}


def parse_reference(url: str) -> dict[str, str] | None:
    """Recognize one exact hosted route; strip query/fragment from its identity.

    Return None for unsupported/malformed inputs. IDs are opaque URL tokens,
    not validated against a live account. No decoding of path separators.
    """
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    try:
        p = urlsplit(url)
        host = p.hostname
        if (p.scheme != "https" or host not in KNOWN_HOSTS or p.username is not None
                or p.password is not None or p.port not in (None, 443)):
            return None
    except ValueError:
        return None
    if host == "github.com":
        pr = re.fullmatch(r"/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]{0,19})/?", p.path)
        if pr and pr[2] not in {".", ".."}:
            repo, number = f"{pr[1]}/{pr[2]}", pr[3]
            return {"provider": "github", "kind": "pull_request_id",
                    "id": f"{repo}#{number}", "repository": repo, "number": number,
                    "url": f"https://github.com/{repo}/pull/{number}"}
    provider, route, kind = _ROUTES[host]
    match = re.fullmatch(r"/" + route + "/(" + _TOKEN + r")/?", p.path)
    if not match or match[1].lower() in _RESERVED:
        return None
    token = match[1]
    if provider == "cursor" and (not token.startswith("bc-") or len(token) == 3):
        return None
    return {"provider": provider, "kind": kind, "id": token,
            "url": f"https://{host}/{route}/{token}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args(argv)
    reference = parse_reference(args.url)
    print(json.dumps({"reference": reference, "content_fetched": False}, ensure_ascii=False))
    return 0 if reference else 1


if __name__ == "__main__":
    raise SystemExit(main())
