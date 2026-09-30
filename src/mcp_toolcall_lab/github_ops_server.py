"""FastMCP server exposing vendored ``gh_ops.py`` GitHub REST operations as MCP tools.

``vendor/gh_ops.py`` (see ``vendor/gh_ops.provenance.json`` and
``docs/github_ops_mcp.md``) is a stdlib-only client for a handful of GitHub
REST endpoints, built for the ``browser-test-kit`` repo's own PR/CI workflow
(myon-bioinformatics/browser-test-kit#10, merged as ``dce1533``). Most tools below are thin wrappers over those functions. The exception is
``resolve_public``: it intentionally owns a small anonymous resolver that
combines GitHub UI/API/raw candidates with GitHub Pages and direct HTTP
evidence. It never reads or accepts a token.

v1 is read-only. ``gh_ops`` also has write operations (``pr_merge``,
``pr_body_replace``, ``pr_body_set``, ``pr_edit``, ``workflow_dispatch``,
``file_put``, ``sync_main``) and a wait loop (``checks_wait``); none of them
are exposed here -- see ``docs/github_ops_mcp.md`` for why, and the follow-up
list.

Auth: the token comes only from the ``GITHUB_TOKEN`` / ``GH_TOKEN``
environment variables, exactly as ``gh_ops.Client()`` already reads them when
constructed with no explicit token. No tool below accepts a token argument,
returns one, or logs one. Known operational/input errors are scrubbed before
they leave this module; unexpected exceptions remain FastMCP tool errors.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from .github_public_resolver import resolve_public_github

REPO_ROOT = Path(__file__).resolve().parents[2]
GH_OPS_PATH = REPO_ROOT / "vendor" / "gh_ops.py"


def gh_ops_py_path() -> Path | None:
    override = os.environ.get("GH_OPS_PY")
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.append(GH_OPS_PATH)
    candidates.append(Path("/app/vendor/gh_ops.py"))
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_gh_ops() -> ModuleType:
    """Load vendored ``gh_ops.py`` by path, the same way ``markdown_lib.load_markdown`` does.

    Not imported as ``mcp_toolcall_lab.vendor.gh_ops`` -- ``vendor/`` is a
    pinned snapshot directory, not a package this project maintains.
    """
    path = gh_ops_py_path()
    if path is None:
        raise FileNotFoundError("vendor/gh_ops.py is missing; see docs/github_ops_mcp.md")
    spec = importlib.util.spec_from_file_location("lab_vendored_gh_ops", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


gh_ops = load_gh_ops()


def _client() -> Any:
    """A fresh ``gh_ops.Client`` per call, reading GITHUB_TOKEN/GH_TOKEN only from the environment."""
    return gh_ops.Client()


def _safe(fn, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run one gh_ops function; turn known operational/input errors into a result.

    ``gh_ops.Client.request`` already scrubs the token out of HTTP-error
    messages (a 401/403/404 becomes ``GhOpsError`` with hint text, never the
    raw token); ``scrub`` runs again here as a second pass over the exception
    text so a stubbed or future error path can't leak a token into a tool
    result either. Known GitHub/input errors become ``ok: False``; unexpected
    exceptions remain FastMCP tool errors so programming defects or malformed
    upstream responses are not disguised as normal results.
    """
    try:
        return fn(*args, **kwargs)
    except gh_ops.GhOpsError as error:
        return {"ok": False, "error": gh_ops.scrub(str(error))}
    except (ValueError, KeyError) as error:
        # Bad input (e.g. a malformed repo string, an out-of-range --show index)
        # is a checked condition, not a crash -- same exit-1-vs-2 split gh_ops's
        # own CLI makes, just without the process exit.
        return {"ok": False, "error": gh_ops.scrub(str(error))}


def create_mcp() -> FastMCP:
    """Build the read-only GitHub-ops MCP server."""
    mcp = FastMCP("mcp-toolcall-lab-github-ops")

    try:
        from .server import ObservabilityMiddleware

        mcp.add_middleware(ObservabilityMiddleware())
    except Exception as error:
        # Tracing is best-effort: MCP_TOOLCALL_LOG is unset by default, and a
        # missing/incompatible fastmcp middleware API must never block the
        # GitHub-ops tools themselves from working.
        if os.environ.get("MCP_TOOLCALL_LOG"):
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            # Exception text may contain credentials or private paths.
            print(
                f"{stamp} WARNING github_ops observability middleware unavailable "
                f"({type(error).__name__}); GitHub tools remain enabled",
                file=sys.stderr,
                flush=True,
            )

    @mcp.tool(
        description=(
            "One-line PR state: open/closed, draft, merged, mergeable/mergeable_state, "
            "head/base refs and SHA, commit/file/line counts, and the PR's web URL."
        )
    )
    def pr_status(repo: str, number: int) -> dict[str, Any]:
        return _safe(gh_ops.pr_status, repo, number, client=_client())

    @mcp.tool(
        description=(
            "PRs whose head is `branch` (`owner:branch` for a fork, else the repo owner). "
            "`ok` is False when there is none -- answers \"is there already a PR for this "
            "branch, and was it merged?\" before creating one."
        )
    )
    def pr_for_branch(repo: str, branch: str, state: str = "all") -> dict[str, Any]:
        return _safe(gh_ops.pr_for_branch, repo, branch, state=state, client=_client())

    @mcp.tool(
        description=(
            "Open PRs across every repository of `owner`, most recently updated first. "
            "Uses GitHub issue search (`is:pr is:open`, `org=True` for `org:OWNER`, "
            "else `user:OWNER`) unless "
            "`repos` is given, in which case each named repository is read through its own "
            "/repos/OWNER/NAME/pulls endpoint instead -- for tokens/proxies that allow "
            "repository-scoped reads but not search. `ok` is False when nothing is open."
        )
    )
    def open_prs(
        owner: str,
        org: bool = False,
        repos: list[str] | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        return _safe(
            gh_ops.open_prs,
            owner,
            org=org,
            repos=tuple(repos or ()),
            limit=limit,
            client=_client(),
        )

    @mcp.tool(
        description=(
            "Digest an issue/PR conversation: one row per comment (index, created_at, author, "
            "character count, a whitespace-collapsed preview, and its URL) -- never the whole "
            "thread. Full bodies are returned only for the indexes listed in `show`; `last` "
            "keeps only the most recent N rows and `author` filters by login, both applied "
            "before `show` is resolved against the *full*, unfiltered comment list (see "
            "gh_ops.issue_comments's `_digest`). Bounded, read-only: no `save`-to-file option "
            "is exposed here."
        )
    )
    def issue_comments_digest(
        repo: str,
        number: int,
        since: str | None = None,
        last: Annotated[int | None, Field(ge=1, le=100)] = None,
        author: str | None = None,
        preview: Annotated[int, Field(ge=1, le=1000)] = 110,
        show: list[int] | None = None,
    ) -> dict[str, Any]:
        return _safe(
            gh_ops.issue_comments,
            repo,
            number,
            since=since,
            last=last,
            author=author,
            preview=preview,
            show=tuple(show or ()),
            client=_client(),
        )

    @mcp.tool(
        description=(
            "Check-run summary for one commit SHA: total/pending/failed/succeeded counts, a "
            "reason when not green, and each run's id/name/status/conclusion/annotations_count. "
            "A single read -- unlike gh_ops's own `checks-wait` CLI command, this never polls or "
            "sleeps, so it returns immediately whatever the current state is."
        )
    )
    def check_runs(
        repo: str,
        sha: str,
        min_checks: Annotated[int, Field(ge=1, le=100)] = 1,
    ) -> dict[str, Any]:
        def _summary(repo: str, sha: str, *, min_checks: int, client: Any) -> dict[str, Any]:
            repo = gh_ops._repo(repo)
            sha = gh_ops._sha(sha)
            run_list = gh_ops._check_runs(client, repo, sha)
            return {"sha": sha, **gh_ops._summarize_checks(run_list, min_checks)}

        return _safe(_summary, repo, sha, min_checks=min_checks, client=_client())

    @mcp.tool(
        description=(
            "List workflow runs for exactly one of a head SHA or a workflow file name "
            "(e.g. ci.yml). Each row has id, name, event, head_sha, status, conclusion, url."
        )
    )
    def workflow_runs(
        repo: str,
        sha: str | None = None,
        workflow: str | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 30,
    ) -> dict[str, Any]:
        return _safe(gh_ops.runs, repo, sha=sha, workflow=workflow, limit=limit, client=_client())

    @mcp.tool(
        description=(
            "Web URL for a PR, or one of its checks/files/commits tabs, plus the matching REST "
            "API URL (files/commits only; checks has none -- check runs are looked up by commit "
            "SHA, not PR number). Pure string building: no network access."
        )
    )
    def url_pr(repo: str, number: int, tab: str | None = None) -> dict[str, Any]:
        return _safe(gh_ops.url_pr, repo, number, tab=tab)

    @mcp.tool(description="Web and REST API URL comparing base...head. Pure string building: no network access.")
    def url_compare(repo: str, base: str, head: str) -> dict[str, Any]:
        return _safe(gh_ops.url_compare, repo, base, head)

    @mcp.tool(
        description=(
            "Web blame URL for `path` at `ref` (optionally anchored to `line`). GitHub has no "
            "REST blame endpoint, so `api` is always null. Pure string building: no network access."
        )
    )
    def url_blame(repo: str, ref: str, path: str, line: int | None = None) -> dict[str, Any]:
        return _safe(gh_ops.url_blame, repo, ref, path, line=line)

    @mcp.tool(
        description=(
            "Web commit-history URL for `path` at `ref`, and the equivalent REST commits query. "
            "Pure string building: no network access."
        )
    )
    def url_history(repo: str, ref: str, path: str) -> dict[str, Any]:
        return _safe(gh_ops.url_history, repo, ref, path)

    @mcp.tool(
        description=(
            "Web and REST API URL listing workflow runs, optionally filtered by workflow file, "
            "branch, event, or status. Pure string building: no network access."
        )
    )
    def url_runs(
        repo: str,
        workflow: str | None = None,
        branch: str | None = None,
        event: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return _safe(gh_ops.url_runs, repo, workflow=workflow, branch=branch, event=event, status=status)

    @mcp.tool(
        description=(
            "Web and REST API search URL. `kind` one of code/issues/pullrequests/repositories/"
            "commits; issues/pullrequests add an `is:issue`/`is:pr` qualifier to `query`. "
            "Pure string building: no network access."
        )
    )
    def url_search(query: str, kind: str = "code") -> dict[str, Any]:
        return _safe(gh_ops.url_search, query, kind=kind)

    @mcp.tool(
        description=(
            "Resolve public GitHub repository/API/Pages and optional raw-content locations, "
            "then anonymously probe them for direct HTTP evidence. Distinguishes reachable, "
            "not_found, auth_required, rate_limited, http_error, and unverified network errors. "
            "No token is accepted or attached; set probe=false to build candidates only."
        )
    )
    def resolve_public(
        repo: Annotated[str, Field(description="Public GitHub repository in owner/name form.")],
        ref: Annotated[str, Field(description="Git ref used for raw/contents candidates.")] = "main",
        path: Annotated[
            str | None,
            Field(description="Optional relative repository path to resolve as raw/contents URLs."),
        ] = None,
        probe: Annotated[
            bool,
            Field(description="When true, anonymously probe each candidate and return HTTP evidence."),
        ] = True,
    ) -> dict[str, Any]:
        return _safe(resolve_public_github, repo, ref=ref, path=path, probe=probe)

    @mcp.tool(
        description=(
            "Direct raw-content URL (raw.githubusercontent.com) for `path` at `ref`, and the "
            "equivalent REST contents API URL. Pure string building: no network access."
        )
    )
    def url_raw(repo: str, ref: str, path: str) -> dict[str, Any]:
        return _safe(gh_ops.url_raw, repo, ref, path)

    return mcp


def run_server(mcp: FastMCP | None = None) -> None:
    server = mcp or create_mcp()
    server.run(
        transport="http",
        host=os.environ.get("GITHUB_OPS_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("GITHUB_OPS_MCP_PORT", "8010")),
        path="/mcp",
    )


mcp = create_mcp()


if __name__ == "__main__":
    run_server(mcp)
