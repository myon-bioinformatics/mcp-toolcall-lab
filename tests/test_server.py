"""ObservabilityMiddleware's session correlation must not fake a shared session."""

from __future__ import annotations

from types import SimpleNamespace

from mcp_toolcall_lab.record import resolve_correlation
from mcp_toolcall_lab.server import _session_id


def _context(session_id: object) -> SimpleNamespace:
    return SimpleNamespace(fastmcp_context=SimpleNamespace(session_id=session_id))


def test_session_id_is_none_when_fastmcp_context_missing() -> None:
    assert _session_id(SimpleNamespace(fastmcp_context=None)) is None


def test_session_id_is_none_when_underlying_session_id_is_none() -> None:
    assert _session_id(_context(None)) is None


def test_session_id_stringifies_a_real_session_id() -> None:
    assert _session_id(_context("sess-real")) == "sess-real"


def test_session_id_is_none_when_stringified_none_leaks_in() -> None:
    assert _session_id(_context("None")) is None
    assert _session_id(_context("")) is None


def test_two_no_session_calls_are_not_correlated() -> None:
    sessions: dict[str, str] = {}
    ctx = _context(None)

    first = resolve_correlation(
        meta={},
        headers={},
        session_id=_session_id(ctx),
        session_chats=sessions,
        mint=lambda: "chat_first",
    )
    second = resolve_correlation(
        meta={},
        headers={},
        session_id=_session_id(ctx),
        session_chats=sessions,
        mint=lambda: "chat_second",
    )

    assert first["chat_id"] == "chat_first"
    assert second["chat_id"] == "chat_second"
    assert first["chat_id_source"] == "minted"
    assert second["chat_id_source"] == "minted"
    assert "session_id" not in first
    assert "session_id" not in second
    assert sessions == {}
    assert "None" not in sessions

    leaked = resolve_correlation(
        meta={},
        headers={},
        session_id="None",
        session_chats=sessions,
        mint=lambda: "chat_leaked",
    )
    leaked_again = resolve_correlation(
        meta={},
        headers={},
        session_id="None",
        session_chats=sessions,
        mint=lambda: "chat_leaked_again",
    )
    assert leaked["chat_id"] == "chat_leaked"
    assert leaked_again["chat_id"] == "chat_leaked_again"
    assert leaked["chat_id_source"] == "minted"
    assert "None" not in sessions
