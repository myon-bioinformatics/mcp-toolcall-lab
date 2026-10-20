"""Offline identities for observed chat/agent URLs; never fetch conversation text."""
from __future__ import annotations

import argparse
import json
import re
from urllib.parse import urlsplit

# Observed routes, not a promise that the service exposes a content API.
_ROUTES = {
    "chatgpt.com": (("chatgpt", "c", "conversation_id"),),
    "gemini.google.com": (("gemini", "app", "conversation_id"), ("gemini", "share", "conversation_id")),
    "claude.ai": (("claude", "chat", "conversation_id"),),
    "m365.cloud.microsoft": (("m365_copilot", "chat/conversation", "conversation_id"),),
    "cursor.com": (("cursor", "agents", "agent_id"),),
    "github.com": (("github_copilot", "copilot/c", "conversation_id"),),
    "chat.deepseek.com": (("deepseek", "a/chat/s", "conversation_id"),),
    "perplexity.ai": (("perplexity", "search", "conversation_id"),),
    "www.perplexity.ai": (("perplexity", "search", "conversation_id"),),
    "jules.google.com": (("jules", "session", "session_id"),),
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
        # Owner logins have no dots; repository names may (`owner/my.repo`).
        # `/issues/N` addresses either an issue or a pull request. This parser
        # stays offline, so that path is issue_or_pull_request_id.
        numbered = re.fullmatch(
            r"/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)/(pull|issues)/([1-9][0-9]{0,19})/?",
            p.path,
        )
        if numbered and numbered[2] not in {".", ".."}:
            repo, number, leaf = f"{numbered[1]}/{numbered[2]}", numbered[4], numbered[3]
            kind = "pull_request_id" if leaf == "pull" else "issue_or_pull_request_id"
            return {"provider": "github", "kind": kind,
                    "id": f"{repo}#{number}", "repository": repo, "number": number,
                    "url": f"https://github.com/{repo}/{leaf}/{number}"}
    for provider, route, kind in _ROUTES[host]:
        if provider == "jules":
            match = re.fullmatch(r"/" + route + r"/(" + _TOKEN + r")(/.*)?", p.path)
        else:
            match = re.fullmatch(r"/" + route + r"/(" + _TOKEN + r")/?", p.path)
        if not match or match[1].lower() in _RESERVED:
            continue
        token = match[1]
        if provider == "cursor" and (not token.startswith("bc-") or len(token) == 3):
            continue
        return {"provider": provider, "kind": kind, "id": token,
                "url": f"https://{host}/{route}/{token}"}
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args(argv)
    reference = parse_reference(args.url)
    print(json.dumps({"reference": reference, "content_fetched": False}, ensure_ascii=False))
    return 0 if reference else 1


if __name__ == "__main__":
    raise SystemExit(main())
