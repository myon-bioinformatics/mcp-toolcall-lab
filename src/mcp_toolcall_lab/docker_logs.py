"""Project ``docker compose logs --timestamps`` into JSONL.

This is not a new protocol. Each row is one container stdout/stderr line,
with the same ``at`` field MCP_TOOLCALL_LOG and the OpenAI mock already use
so the streams can be merged by time. ``level`` is copied from the line when
the product (or uvicorn) printed one; it is left missing when the line has
none.

    python -m mcp_toolcall_lab.docker_logs capture -f docker/openwebui-smoke/docker-compose.yml -o test-results/docker-logs
    python -m mcp_toolcall_lab.docker_logs timeline --dir test-results -o test-results/timeline.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp_toolcall_lab.mock.common import read_jsonl

SOURCE = "docker-compose"

# docker compose logs --timestamps:
#   open-webui-1  | 2026-09-19T12:55:10.154082123Z message
COMPOSE_LINE = re.compile(
    r"^(?P<container>[A-Za-z0-9][A-Za-z0-9_.-]*)\s+\|\s+"
    r"(?:(?P<ts>\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z)\s+)?"
    r"(?P<message>.*)$"
)
LEVEL_TOKEN = re.compile(
    r"(?:^|[\s\[\]\"'=])(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|TRACE|FATAL)(?:[\s\]:\"']|$)",
    re.IGNORECASE,
)
JSON_LEVEL = re.compile(r'"(?:level|severity)"\s*:\s*"(?P<level>[A-Za-z]+)"', re.IGNORECASE)
KNOWN_SERVICES = (
    "mcp-mock",
    "openai-mock",
    "open-webui",
    "librechat",
    "stub-front",
    "cpu-llm",
)
_LEVEL_ALIAS = {
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARNING",
    "warn": "WARNING",
    "error": "ERROR",
    "critical": "CRITICAL",
    "fatal": "CRITICAL",
    "trace": "DEBUG",
}
_REDACT = (
    (re.compile(r"(?i)(bearer)\s+\S+"), r"\1 [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-[REDACTED]"),
)


def redact(text: str) -> str:
    out = text
    for pattern, repl in _REDACT:
        out = pattern.sub(repl, out)
    return out


def normalize_level(token: str | None) -> str | None:
    if not token:
        return None
    return _LEVEL_ALIAS.get(token.strip().lower())


def parse_level(message: str) -> str | None:
    json_hit = JSON_LEVEL.search(message)
    if json_hit:
        level = normalize_level(json_hit.group("level"))
        if level:
            return level
    hit = LEVEL_TOKEN.search(message)
    if hit:
        return normalize_level(hit.group("level"))
    return None


def service_from_container(container: str) -> str:
    for service in KNOWN_SERVICES:
        if service in container:
            return service
    if "-" in container and container.rsplit("-", 1)[-1].isdigit():
        return container.rsplit("-", 1)[0]
    return container


def parse_compose_line(line: str) -> dict[str, Any] | None:
    text = line.rstrip("\n")
    if not text.strip():
        return None
    match = COMPOSE_LINE.match(text)
    if match:
        container = match.group("container")
        message = redact(match.group("message") or "")
        row: dict[str, Any] = {
            "at": match.group("ts"),
            "service": service_from_container(container),
            "container": container,
            "message": message,
            "source": SOURCE,
        }
        level = parse_level(message)
        if level:
            row["level"] = level
        return row
    message = redact(text)
    row = {"at": None, "service": None, "container": None, "message": message, "source": SOURCE}
    level = parse_level(message)
    if level:
        row["level"] = level
    return row


def parse_compose_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        row = parse_compose_line(line)
        if row:
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def record_compose_image(compose_file: Path, service: str, out_path: Path) -> dict[str, Any]:
    """Write docker inspect of one compose service (digest / image id)."""
    cid = subprocess.check_output(
        ["docker", "compose", "-f", str(compose_file), "ps", "-q", service],
        text=True,
    ).strip().splitlines()
    if not cid or not cid[0]:
        raise RuntimeError(f"no container for compose service {service}")
    inspect = json.loads(
        subprocess.check_output(["docker", "inspect", cid[0]], text=True)
    )[0]
    image_name = (inspect.get("Config") or {}).get("Image")
    repo_digests = list(inspect.get("RepoDigests") or [])
    image_id = inspect.get("Image")
    if image_name:
        try:
            img = json.loads(
                subprocess.check_output(["docker", "image", "inspect", image_name], text=True)
            )[0]
            image_id = img.get("Id") or image_id
            for digest in img.get("RepoDigests") or []:
                if digest not in repo_digests:
                    repo_digests.append(digest)
        except Exception:
            pass
    row = {
        "service": service,
        "image": image_name,
        "id": inspect.get("Id"),
        "image_id": image_id,
        "repo_digests": repo_digests,
        "created": inspect.get("Created"),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
    return row


def capture_compose_logs(compose_file: Path, out_dir: Path) -> dict[str, Any]:
    """Run ``docker compose logs --timestamps`` and write raw + JSONL."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "logs",
        "--timestamps",
        "--no-color",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    raw = proc.stdout or ""
    err = proc.stderr or ""
    (out_dir / "compose.log").write_text(redact(raw), encoding="utf-8")
    if err.strip():
        (out_dir / "compose.err").write_text(redact(err), encoding="utf-8")
    rows = parse_compose_text(raw)
    jsonl_path = out_dir / "compose.jsonl"
    write_jsonl(jsonl_path, rows)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "lines": len(rows),
        "raw": str(out_dir / "compose.log"),
        "jsonl": str(jsonl_path),
    }


def _stream_kind(path: Path) -> str:
    name = path.name.lower()
    if "compose" in name or "docker" in name:
        return SOURCE
    if "mcp" in name:
        return "mcp"
    if "openai" in name:
        return "openai"
    if "antipattern" in name:
        return "antipattern"
    return path.stem


def timeline_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        kind = _stream_kind(path)
        if path.suffix == ".log":
            events = parse_compose_text(path.read_text(encoding="utf-8"))
        else:
            events = read_jsonl(path)
        for event in events:
            row = dict(event)
            row.setdefault("stream", kind)
            rows.append(row)
    rows.sort(key=lambda item: str(item.get("at") or ""))
    return rows


def default_timeline_paths(directory: Path) -> list[Path]:
    candidates = [
        directory / "docker-logs" / "compose.jsonl",
        directory / "mcp-toolcalls.jsonl",
        directory / "openai-mock.jsonl",
        directory / "antipatterns.jsonl",
    ]
    return [path for path in candidates if path.is_file()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mcp_toolcall_lab.docker_logs",
        description="Capture docker compose logs and merge them with MCP/OpenAI JSONL by time.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    capture = sub.add_parser("capture", help="docker compose logs --timestamps → raw + JSONL")
    capture.add_argument("--compose", "-f", required=True, type=Path)
    capture.add_argument("--out", "-o", required=True, type=Path)

    timeline = sub.add_parser("timeline", help="interleave JSONL streams by ``at``")
    timeline.add_argument("--dir", type=Path, default=Path("test-results"))
    timeline.add_argument("--out", "-o", type=Path, default=None)
    timeline.add_argument("paths", nargs="*", type=Path)

    parse = sub.add_parser("parse", help="parse a saved compose.log to JSONL on stdout")
    parse.add_argument("log", type=Path)

    image = sub.add_parser("image", help="write docker inspect digest/id for one compose service")
    image.add_argument("--compose", "-f", required=True, type=Path)
    image.add_argument("--service", required=True)
    image.add_argument("--out", "-o", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "capture":
        summary = capture_compose_logs(args.compose, args.out)
        print(json.dumps(summary, indent=2))
        return 0 if summary["ok"] else 1
    if args.cmd == "parse":
        rows = parse_compose_text(args.log.read_text(encoding="utf-8"))
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.cmd == "image":
        row = record_compose_image(args.compose, args.service, args.out)
        print(json.dumps(row, indent=2))
        return 0
    paths = [Path(item) for item in args.paths] or default_timeline_paths(args.dir)
    rows = timeline_rows(paths)
    if args.out:
        write_jsonl(args.out, rows)
        print(json.dumps({"out": str(args.out), "rows": len(rows)}))
    else:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
