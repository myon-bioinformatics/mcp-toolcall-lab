"""Stdlib tests for the ID trace probe — no MCP server, no browser."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp_toolcall_lab.trace_probe import (
    FoundId,
    Hop,
    classify_value,
    cluster_hops,
    collect_hops,
    extract_ids_from_text,
    extract_ids_from_url,
    kinds_catalog,
    main,
    probe,
    resume_chat_path,
    snapshot_trace,
)

ROOT = Path(__file__).resolve().parents[1]


def test_kinds_cover_reasoning_and_response_prefixes() -> None:
    kinds = {row["kind"] for row in kinds_catalog()}
    assert {"lab_chat_id", "call_id", "completion_id", "response_id", "reasoning_id"}.issubset(kinds)
    assert classify_value("chat_ab" + "c" * 20) == "lab_chat_id"
    assert classify_value("call_" + "a" * 24) == "call_id"
    assert classify_value("chatcmpl-abc123") == "completion_id"
    assert classify_value("resp_abc123") == "response_id"
    assert classify_value("rs_abc123") == "reasoning_id"
    assert classify_value("msg_abc123") == "responses_message_id"
    assert classify_value("fc_abc123") == "function_call_item_id"
    assert classify_value("66f012345678901234567890", key="chat_id") == "conversation_id"
    assert classify_value("chat_" + "d" * 24, key="chat_id") == "lab_chat_id"
    assert classify_value("sess-abcd-efgh", key="session_id") == "mcp_session_id"
    assert classify_value("req-initialize-1", key="request_id") == "mcp_request_id"


def test_resume_path_only_for_product_ids() -> None:
    assert resume_chat_path(None) is None
    assert resume_chat_path("new") is None
    assert resume_chat_path("chat_" + "a" * 24) is None
    assert resume_chat_path("chatcmpl-xyz") is None
    assert resume_chat_path("66f012345678901234567890") == "/c/66f012345678901234567890"


def test_url_harvests_librechat_and_owui_ids() -> None:
    libre = extract_ids_from_url(
        "http://127.0.0.1:3080/c/66f012345678901234567890/msgAAA?parentMessageId=parent1"
    )
    kinds = {item.kind: item.value for item in libre}
    assert kinds["conversation_id"] == "66f012345678901234567890"
    assert kinds["ui_message_id"] in {"msgAAA", "parent1"}
    assert any(item.kind == "parent_message_id" and item.value == "parent1" for item in libre)

    share = extract_ids_from_url("http://127.0.0.1:3000/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert share[0].kind == "share_id"
    assert extract_ids_from_url("http://127.0.0.1:3080/c/new") == []


def test_text_harvests_prefixed_ids() -> None:
    found = extract_ids_from_text(
        "reasoning rs_aaa then resp_bbb and leftover chatcmpl-ccc plus call_dddddddd"
    )
    by_kind = {item.kind: item.value for item in found}
    assert by_kind["reasoning_id"] == "rs_aaa"
    assert by_kind["response_id"] == "resp_bbb"
    assert by_kind["completion_id"] == "chatcmpl-ccc"
    assert by_kind["call_id"] == "call_dddddddd"


def test_shared_call_id_joins_mcp_and_openai_hops(tmp_path: Path) -> None:
    mcp = tmp_path / "mcp.jsonl"
    openai = tmp_path / "openai.jsonl"
    mcp.write_text(
        json.dumps(
            {
                "at": "2026-09-19T00:00:00+00:00",
                "tool": "find_municipalities",
                "outcome": "success",
                "meta": {
                    "chat_id": "chat_" + "a" * 24,
                    "call_id": "call_" + "b" * 24,
                    "source": "chat",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    openai.write_text(
        json.dumps(
            {
                "kind": "chat.completions",
                "completion_id": "chatcmpl-xyz",
                "call_ids": ["call_" + "b" * 24],
                "user": "Find municipalities named Yokohama",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    hops = collect_hops(mcp_log=mcp, openai_log=openai)
    report = probe(hops, needles={"chat_id": "chat_" + "a" * 24})
    assert report["matched"] is True
    assert report["match"] == "all"
    kinds = report["clusters"][0]["by_kind"]
    assert kinds["lab_chat_id"] == ["chat_" + "a" * 24]
    assert kinds["call_id"] == ["call_" + "b" * 24]
    assert kinds["completion_id"] == ["chatcmpl-xyz"]


def test_chat_id_plus_url_attaches_without_pretending_a_log_hit() -> None:
    hops = collect_hops(
        page_url="http://127.0.0.1:3080/c/66f012345678901234567890",
        labels={"lab_chat_id": "chat_" + "c" * 24},
    )
    report = probe(hops, needles={"chat_id": "chat_" + "c" * 24})
    assert report["matched"] is True
    kinds = report["clusters"][0]["by_kind"]
    assert kinds["conversation_id"] == ["66f012345678901234567890"]
    assert kinds["lab_chat_id"] == ["chat_" + "c" * 24]


def test_unknown_chat_id_does_not_match_empty_logs(tmp_path: Path) -> None:
    hops = collect_hops(mcp_log=tmp_path / "missing.jsonl")
    report = probe(hops, needles={"chat_id": "chat_" + "f" * 24})
    assert report["matched"] is False
    assert report["clusters"] == []


def test_snapshot_includes_labels_even_without_url() -> None:
    snap = snapshot_trace(labels={"lab_chat_id": "chat_" + "e" * 24, "trace_id": "probe-9"})
    assert snap["by_kind"]["lab_chat_id"] == ["chat_" + "e" * 24]
    assert snap["by_kind"]["trace_id"] == ["probe-9"]


def test_orphan_hops_stay_separate() -> None:
    hops = [
        Hop(source="mcp_log", ids=[FoundId("lab_chat_id", "chat_" + "1" * 24, "mcp_log")]),
        Hop(source="openai_log", ids=[FoundId("completion_id", "chatcmpl-other", "openai_log")]),
    ]
    clusters = cluster_hops(hops)
    assert len(clusters) == 2


def test_cli_kinds_and_probe(tmp_path: Path) -> None:
    mcp = tmp_path / "mcp.jsonl"
    mcp.write_text(
        json.dumps({"meta": {"chat_id": "chat_" + "a" * 24, "call_id": "call_" + "b" * 24}}) + "\n",
        encoding="utf-8",
    )
    kinds_proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolcall_lab.trace_probe", "kinds"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(kinds_proc.stdout)
    assert any(row["kind"] == "reasoning_id" for row in payload["kinds"])

    probe_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_toolcall_lab.trace_probe",
            "--chat-id",
            "chat_" + "a" * 24,
            "--mcp-log",
            str(mcp),
            "--openai-log",
            str(tmp_path / "missing-openai.jsonl"),
            "--observe",
            str(tmp_path / "missing-obs.jsonl"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(probe_proc.stdout)
    assert probe_proc.returncode == 0
    assert report["matched"] is True
    assert report["clusters"][0]["by_kind"]["call_id"] == ["call_" + "b" * 24]


def test_main_exit_code_on_miss(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--chat-id",
            "chat_" + "z" * 24,
            "--mcp-log",
            str(tmp_path / "nope.jsonl"),
            "--openai-log",
            str(tmp_path / "nope-o.jsonl"),
            "--observe",
            str(tmp_path / "nope-a.jsonl"),
        ]
    )
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["matched"] is False
