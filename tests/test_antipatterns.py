from __future__ import annotations

from pathlib import Path

from mcp_toolcall_lab.antipatterns import (
    AUTH_BLOCKED,
    KNOWN_IDS,
    MCP_NOT_CALLED,
    MCP_PICKER_OFF,
    UI_NO_RESULT,
    VERDICT_ANTIPATTERN,
    VERDICT_PASS,
    classify_observation,
    write_observation,
)

CATALOG = Path(__file__).resolve().parents[1] / "fixtures" / "antipatterns" / "catalog.yaml"


def test_catalog_lists_every_known_id() -> None:
    text = CATALOG.read_text(encoding="utf-8")
    for antipattern_id in sorted(KNOWN_IDS):
        assert f"id: {antipattern_id}" in text, antipattern_id


def test_classify_pass_when_mcp_and_ui_agree() -> None:
    result = classify_observation(
        input_found=True,
        send_clicked=True,
        logged_in=True,
        assistant_visible=True,
        openai_saw_tools=True,
        mcp_calls=[{"tool": "find_municipalities", "outcome": "success"}],
        ui_text="Yokohama (code 14109) is in Kanagawa.",
    )
    assert result["verdict"] == VERDICT_PASS
    assert result["antipattern_id"] is None


def test_classify_picker_off_and_mcp_not_called() -> None:
    picker = classify_observation(
        input_found=True,
        send_clicked=True,
        logged_in=True,
        assistant_visible=True,
        openai_saw_tools=False,
        mcp_calls=[],
        ui_text="No MCP tools were attached",
    )
    assert picker["antipattern_id"] == MCP_PICKER_OFF

    missing = classify_observation(
        input_found=True,
        send_clicked=True,
        logged_in=True,
        assistant_visible=True,
        openai_saw_tools=True,
        mcp_calls=[],
        ui_text="something else",
    )
    assert missing["antipattern_id"] == MCP_NOT_CALLED


def test_classify_auth_and_ui_gap() -> None:
    assert (
        classify_observation(
            input_found=False,
            send_clicked=False,
            logged_in=False,
            assistant_visible=False,
            openai_saw_tools=None,
            mcp_calls=[],
            ui_text="",
        )["antipattern_id"]
        == AUTH_BLOCKED
    )
    gap = classify_observation(
        input_found=True,
        send_clicked=True,
        logged_in=True,
        assistant_visible=True,
        openai_saw_tools=True,
        mcp_calls=[{"tool": "find_municipalities", "outcome": "success"}],
        ui_text="an error occurred",
    )
    assert gap["antipattern_id"] == UI_NO_RESULT
    assert gap["verdict"] == VERDICT_ANTIPATTERN


def test_write_observation_appends_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "observed.jsonl"
    write_observation(path, observation={"verdict": VERDICT_ANTIPATTERN, "antipattern_id": MCP_NOT_CALLED})
    write_observation(path, observation={"verdict": VERDICT_PASS, "antipattern_id": None})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "MCP_NOT_CALLED" in lines[0]
    assert "PASS" in lines[1]
