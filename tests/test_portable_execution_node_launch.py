"""Parity between legacy subprocess Node bridge and vendored node_launch."""
from __future__ import annotations

import errno
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import NamedTuple, Union

import pytest

from mcp_toolcall_lab import portable_execution as pe

ROOT = Path(__file__).resolve().parents[1]
CORE = pe.CORE
_STDIN_CLOSED_PAYLOAD = b"x" * 70000 + b"\n"
_LARGE_INPUT = b"k: v\n" + b"#" * (4 * 1024 * 1024 - 5)
_OVER_CAP_BYTES = 5_000_000


class _FakeNodeSpec(NamedTuple):
    version: str
    on_eval: str
    close_stdin_unread: bool = False
    stdin_close_exit_code: int = 3
    stdin_close_stdout: bytes = b""
    stdout_bytes: bytes = b""


_FAKE_NODE_SPECS: dict[str, _FakeNodeSpec] = {}


def _fake_node_key(path: Union[Path, str]) -> str:
    return str(Path(path).resolve())


class _StdinClosesEarly:
    def __init__(self, pipe_byte_limit: int = 4096) -> None:
        self._limit = pipe_byte_limit
        self._written = 0

    def write(self, data: bytes) -> int:
        if self._written >= self._limit:
            raise BrokenPipeError()
        remaining = self._limit - self._written
        if len(data) > remaining:
            self._written = self._limit
            raise BrokenPipeError()
        self._written += len(data)
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def _run_fake_on_eval(on_eval: str) -> tuple[bytes, bytes, int]:
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    code = 0
    try:
        sys.stdout = stdout
        sys.stderr = stderr
        exec(on_eval, {"sys": sys})  # noqa: S102
    except SystemExit as exc:
        if exc.code is None:
            code = 0
        elif isinstance(exc.code, int):
            code = exc.code
        else:
            code = 1
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return (
        stdout.getvalue().encode("utf-8", errors="replace"),
        stderr.getvalue().encode("utf-8", errors="replace"),
        code,
    )


class _FakeNodePopen:
    def __init__(
        self,
        command: list[str],
        on_eval: str,
        *,
        close_stdin_unread: bool = False,
        stdin_close_exit_code: int = 3,
        stdin_close_stdout: bytes = b"",
        stdout_bytes: bytes = b"",
        **_kwargs: object,
    ) -> None:
        self.args = command
        self._on_eval = on_eval
        self._close_stdin_unread = close_stdin_unread
        self._stdin_close_exit_code = stdin_close_exit_code
        self._stdin_close_stdout = stdin_close_stdout
        self._stdout_bytes = stdout_bytes
        stdout_r, stdout_w = os.pipe()
        stderr_r, stderr_w = os.pipe()
        if close_stdin_unread:
            self.stdin = _StdinClosesEarly()
            self._stdin_r = None
        else:
            stdin_r, stdin_w = os.pipe()
            self.stdin = os.fdopen(stdin_w, "wb")
            self._stdin_r = stdin_r
        self.stdout = os.fdopen(stdout_r, "rb")
        self.stderr = os.fdopen(stderr_r, "rb")
        self._stdout_w = stdout_w
        self._stderr_w = stderr_w
        self.returncode: int | None = None
        self._done = threading.Event()

        def worker() -> None:
            if self._close_stdin_unread:
                try:
                    if self._stdin_close_stdout:
                        os.write(self._stdout_w, self._stdin_close_stdout)
                    if self._stdin_close_exit_code != 0:
                        os.write(self._stderr_w, b"boom\n")
                finally:
                    os.close(self._stdout_w)
                    os.close(self._stderr_w)
                    self.returncode = self._stdin_close_exit_code
                    self._done.set()
                return

            def drain_stdin() -> None:
                try:
                    with os.fdopen(self._stdin_r, "rb") as stream:
                        while stream.read(65536):
                            pass
                except OSError:
                    pass

            threading.Thread(target=drain_stdin, daemon=True).start()
            stdout, stderr, rc = _run_fake_on_eval(self._on_eval)
            if self._stdout_bytes:
                stdout = self._stdout_bytes
            try:
                if stdout:
                    os.write(self._stdout_w, stdout)
                if stderr:
                    os.write(self._stderr_w, stderr)
            finally:
                os.close(self._stdout_w)
                os.close(self._stderr_w)
                self.returncode = rc
                self._done.set()

        threading.Thread(target=worker, daemon=True).start()

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode if self.returncode is not None else -1

    def kill(self) -> None:
        self.returncode = -9
        self._done.set()


def _complete_fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess[bytes]:
    spec = _FAKE_NODE_SPECS[_fake_node_key(argv[0])]
    proc = _FakeNodePopen(
        argv,
        spec.on_eval,
        close_stdin_unread=spec.close_stdin_unread,
        stdin_close_exit_code=spec.stdin_close_exit_code,
        stdin_close_stdout=spec.stdin_close_stdout,
        stdout_bytes=spec.stdout_bytes,
    )
    input_data = kwargs.get("input")
    if input_data is not None:
        payload = input_data.encode("utf-8") if isinstance(input_data, str) else input_data
        if proc.stdin is not None and hasattr(proc.stdin, "write"):
            try:
                proc.stdin.write(payload)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
    timeout = kwargs.get("timeout")
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def read_stdout() -> None:
        if proc.stdout:
            stdout_chunks.append(proc.stdout.read())

    def read_stderr() -> None:
        if proc.stderr:
            stderr_chunks.append(proc.stderr.read())

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    stdout_thread.join()
    stderr_thread.join()
    stdout = stdout_chunks[0] if stdout_chunks else b""
    stderr = stderr_chunks[0] if stderr_chunks else b""
    return subprocess.CompletedProcess(tuple(argv), rc, stdout, stderr)


@pytest.fixture(autouse=True)
def _node_launch_process_layer_stub(monkeypatch):
    node_launch = pe._node_launch_module()
    real_run = node_launch.subprocess.run
    real_popen = node_launch.subprocess.Popen

    def fake_run(args, *pargs, **kwargs):
        if args and not isinstance(args, (str, bytes)):
            argv = list(args)
        else:
            argv = [args, *pargs]
        spec = _FAKE_NODE_SPECS.get(_fake_node_key(argv[0])) if argv else None
        if spec is not None and len(argv) >= 2 and argv[1] == "--version":
            return subprocess.CompletedProcess(
                tuple(argv), 0, (spec.version + "\n").encode(), b""
            )
        if spec is not None and not (len(argv) >= 2 and argv[1] == "--version"):
            return _complete_fake_run(argv, **kwargs)
        return real_run(args, *pargs, **kwargs)

    def fake_popen(command, **kwargs):
        argv = [os.fspath(part) for part in command]
        spec = _FAKE_NODE_SPECS.get(_fake_node_key(argv[0])) if argv else None
        if spec is None:
            return real_popen(command, **kwargs)
        return _FakeNodePopen(
            argv,
            spec.on_eval,
            close_stdin_unread=spec.close_stdin_unread,
            stdin_close_exit_code=spec.stdin_close_exit_code,
            stdin_close_stdout=spec.stdin_close_stdout,
            stdout_bytes=spec.stdout_bytes,
            **kwargs,
        )

    monkeypatch.setattr(node_launch.subprocess, "run", fake_run)
    monkeypatch.setattr(node_launch.subprocess, "Popen", fake_popen)
    yield
    _FAKE_NODE_SPECS.clear()


def _write_fake_node(
    tmp_path: Path,
    *,
    version: str,
    on_eval: str = "pass",
    close_stdin_unread: bool = False,
    stdin_close_exit_code: int = 3,
    stdin_close_stdout: bytes = b"",
    stdout_bytes: bytes = b"",
) -> Path:
    name = "fake_node.exe" if os.name == "nt" else "fake_node"
    dest = tmp_path / name
    dest.write_bytes(b"")
    _FAKE_NODE_SPECS[_fake_node_key(dest)] = _FakeNodeSpec(
        version,
        on_eval,
        close_stdin_unread=close_stdin_unread,
        stdin_close_exit_code=stdin_close_exit_code,
        stdin_close_stdout=stdin_close_stdout,
        stdout_bytes=stdout_bytes,
    )
    return dest


def _legacy_portable_node_run(
    payload: bytes,
    *,
    node: str | Path | None = None,
    timeout: float = pe.NODE_BRIDGE_TIMEOUT,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    if node is not None:
        executable = os.fspath(node)
    else:
        executable = os.environ.get("NODE", "node")
    command = [executable, str(CORE), "--native-only"]
    spec = _FAKE_NODE_SPECS.get(_fake_node_key(executable))
    if spec is not None:
        proc = _complete_fake_run(command, input=payload, timeout=timeout)
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, proc.args, proc.stdout, proc.stderr
            )
        return proc
    return subprocess.run(
        command,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=check,
    )


def _proc_bytes(proc: subprocess.CompletedProcess[bytes]) -> tuple[int, bytes, bytes]:
    return proc.returncode, proc.stdout, proc.stderr


def _assert_same_outcome(old_call, new_call):
    try:
        old_value = old_call()
    except BaseException as old_exc:
        try:
            new_call()
        except BaseException as new_exc:
            assert type(old_exc) is type(new_exc), (old_exc, new_exc)
            if isinstance(old_exc, subprocess.TimeoutExpired):
                assert new_exc.timeout == old_exc.timeout
                assert new_exc.cmd == old_exc.cmd
                assert new_exc.output == old_exc.output
                assert new_exc.stderr == old_exc.stderr
            elif isinstance(old_exc, subprocess.CalledProcessError):
                assert new_exc.returncode == old_exc.returncode
                assert new_exc.stdout == old_exc.stdout
                assert new_exc.stderr == old_exc.stderr
            elif isinstance(old_exc, FileNotFoundError):
                assert new_exc.errno == old_exc.errno
            else:
                assert str(old_exc) == str(new_exc), (old_exc, new_exc)
            return
        raise AssertionError(f"legacy raised {old_exc!r}, new succeeded")
    new_value = new_call()
    assert _proc_bytes(old_value) == _proc_bytes(new_value)


def _legacy_execute_over_cap(request: dict, node: Path) -> dict:
    encoded = pe.canonical_request(request).encode()
    completed = _legacy_portable_node_run(encoded, node=node, check=True)
    if len(completed.stdout) > pe.NODE_RECEIPT_MAX_BYTES:
        return pe.failure("bridge_failed", "Node receipt exceeds 4 MiB.")
    raise AssertionError("expected over-cap stdout")


def test_receipt_over_cap_matches_legacy_execute_message(tmp_path, monkeypatch):
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        on_eval="pass",
        stdout_bytes=b"x" * _OVER_CAP_BYTES,
    )
    monkeypatch.setenv("NODE", str(script))
    pe._node_launch_module().clear_probe_cache()
    request = {
        "schema": pe.CONTRACT,
        "operation": "trace_summary",
        "input": {"jsonl": ""},
    }
    legacy = _legacy_execute_over_cap(request, script)
    assert legacy["error"]["message"] == "Node receipt exceeds 4 MiB."
    assert pe.execute(request) == legacy


def test_normal_run_matches_legacy(tmp_path):
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        on_eval="import sys; sys.stdout.write('ok\\n')",
    )
    payload = b'{"schema":"lab-execution/1","operation":"trace_summary","input":{"jsonl":""}}'

    def old():
        return _legacy_portable_node_run(payload, node=script, check=False)

    def new():
        return pe._portable_node_run(payload, node=script, check=False)

    _assert_same_outcome(old, new)


def test_explicit_missing_node_path_raises_file_not_found(tmp_path):
    missing = tmp_path / "definitely-not-node"
    assert not missing.exists()
    payload = b"x"

    def old():
        return _legacy_portable_node_run(payload, node=missing, check=False)

    def new():
        return pe._portable_node_run(payload, node=missing, check=False)

    with pytest.raises(FileNotFoundError) as old_exc:
        old()
    with pytest.raises(FileNotFoundError) as new_exc:
        new()
    assert new_exc.value.errno == errno.ENOENT
    assert str(missing) in (new_exc.value.filename or "")


def test_node_not_on_path_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("NODE", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(shutil, "which", lambda name, **kw: None)
    pe._node_launch_module().clear_probe_cache()
    payload = b"x"
    with pytest.raises(FileNotFoundError):
        _legacy_portable_node_run(payload, check=False)
    with pytest.raises(RuntimeError, match=r"Node 24\+.*not found on PATH"):
        pe._portable_node_run(payload, check=False)


def test_node_v20_rejected_while_legacy_would_run(tmp_path):
    script = _write_fake_node(
        tmp_path,
        version="v20.0.0",
        on_eval="import sys; sys.stdout.write('legacy\\n')",
    )
    payload = b"x"
    legacy = _legacy_portable_node_run(payload, node=script, check=False)
    assert legacy.stdout == b"legacy\n"
    with pytest.raises(RuntimeError, match=r"Node 24\+.*found Node 20"):
        pe._portable_node_run(payload, node=script, check=False)


def test_node_v20_on_path_names_found_version(tmp_path, monkeypatch):
    script = _write_fake_node(tmp_path, version="v20.0.0", on_eval="pass")
    monkeypatch.delenv("NODE", raising=False)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, **kw: str(script) if name == "node" else None,
    )
    pe._node_launch_module().clear_probe_cache()
    with pytest.raises(RuntimeError, match=r"Node 24\+.*found Node 20"):
        pe._portable_node_run(b"x", check=False)


def test_nonzero_exit_matches_legacy(tmp_path):
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        on_eval="import sys; sys.stderr.write('diag\\n'); sys.exit(3)",
    )
    payload = b"x"

    def old():
        return _legacy_portable_node_run(payload, node=script, check=False)

    def new():
        return pe._portable_node_run(payload, node=script, check=False)

    _assert_same_outcome(old, new)


def test_nonzero_exit_with_check_raises_called_process_error(tmp_path):
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        on_eval="import sys; sys.stderr.write('diag\\n'); sys.exit(3)",
    )
    payload = b"x"

    def old():
        return _legacy_portable_node_run(payload, node=script, check=True)

    def new():
        return pe._portable_node_run(payload, node=script, check=True)

    _assert_same_outcome(old, new)


def test_timeout_cmd_includes_resolved_node(tmp_path, monkeypatch):
    script = _write_fake_node(tmp_path, version="v24.0.0", on_eval="import time; time.sleep(60)")
    node_launch = pe._node_launch_module()

    def hang(*args, **kwargs):
        raise node_launch.LaunchError(
            "node_launch: Node process timed out",
            code="ELAUNCHFAILURE",
            stdout=b"",
            stderr=b"",
        )

    monkeypatch.setattr(node_launch, "run", hang)
    with pytest.raises(subprocess.TimeoutExpired) as info:
        pe._portable_node_run(b"x", node=script, timeout=7, check=False)
    assert info.value.timeout == 7
    assert info.value.cmd == [str(script), str(CORE), "--native-only"]


def test_timeout_matches_legacy(monkeypatch, tmp_path):
    script = _write_fake_node(tmp_path, version="v24.0.0", on_eval="pass")
    cmd = [str(script), str(CORE), "--native-only"]
    timeout = 11.0
    node_launch = pe._node_launch_module()

    def launch_timeout(*args, **kwargs):
        raise node_launch.LaunchError(
            "node_launch: Node process timed out",
            code="ELAUNCHFAILURE",
            stdout=b"out\r\nline\r",
            stderr=b"err\r\n",
        )

    monkeypatch.setattr(node_launch, "run", launch_timeout)

    def old():
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output=b"out\r\nline\r", stderr=b"err\r\n")

    def new():
        return pe._portable_node_run(b"x", node=script, timeout=timeout, check=False)

    _assert_same_outcome(old, new)


def test_stdin_closed_nonzero_matches_legacy(tmp_path):
    script = _write_fake_node(tmp_path, version="v24.0.0", close_stdin_unread=True)
    payload = _STDIN_CLOSED_PAYLOAD

    def old():
        return _legacy_portable_node_run(payload, node=script, check=False)

    def new():
        return pe._portable_node_run(payload, node=script, check=False)

    old_proc = old()
    new_proc = new()
    assert _proc_bytes(old_proc) == _proc_bytes(new_proc)
    assert new_proc.stderr == b"boom\n"


def test_stdin_closed_zero_exit_returns_stdout(tmp_path):
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        close_stdin_unread=True,
        stdin_close_exit_code=0,
        stdin_close_stdout=b"done\n",
    )
    payload = _STDIN_CLOSED_PAYLOAD

    def old():
        return _legacy_portable_node_run(payload, node=script, check=False)

    def new():
        return pe._portable_node_run(payload, node=script, check=False)

    _assert_same_outcome(old, new)


def test_stdin_closed_zero_exit_with_large_input(tmp_path):
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        close_stdin_unread=True,
        stdin_close_exit_code=0,
        stdin_close_stdout=b'{"ok":true}',
    )
    assert pe._portable_node_run(_LARGE_INPUT, node=script, check=False).stdout == b'{"ok":true}'


def test_node_env_var_honoured_when_unset_path(tmp_path, monkeypatch):
    monkeypatch.setenv("NODE", str(tmp_path / "nonexistent-x"))
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        on_eval="import sys; sys.stdout.write('via-node\\n')",
    )
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, **kw: str(script) if name == "node" else None,
    )
    pe._node_launch_module().clear_probe_cache()
    with pytest.raises(FileNotFoundError):
        pe._portable_node_run(b"x", check=False)


def test_node_env_var_used_when_set_to_fake(tmp_path, monkeypatch):
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        on_eval="import sys; sys.stdout.write('via\\n')",
    )
    monkeypatch.setenv("NODE", str(script))
    pe._node_launch_module().clear_probe_cache()
    done = pe._portable_node_run(b"x", check=False)
    assert done.stdout == b"via\n"


def _node_reader_ready() -> bool:
    node_launch = pe._node_launch_module()
    try:
        node_launch.resolve_node(env_var="NODE")
    except node_launch.NodeNotFoundError:
        return False
    return True


@pytest.mark.skipif(not _node_reader_ready(), reason="Node 24+ required")
def test_real_node_trace_summary_roundtrip():
    payload = (
        b'{"schema":"lab-execution/1","operation":"trace_summary","input":{"jsonl":'
        b'"{\\"event\\":\\"tools/call\\",\\"outcome\\":\\"success\\"}\\n"}}'
    )
    legacy = _legacy_portable_node_run(payload, check=False)
    new = pe._portable_node_run(payload, check=False)
    assert _proc_bytes(legacy) == _proc_bytes(new)


def test_mutation_timeout_mapping_required(tmp_path, monkeypatch):
    script = _write_fake_node(tmp_path, version="v24.0.0", on_eval="pass")
    node_launch = pe._node_launch_module()

    def launch_timeout(*args, **kwargs):
        raise node_launch.LaunchError(
            "node_launch: Node process timed out",
            code="ELAUNCHFAILURE",
            stdout=b"",
            stderr=b"",
        )

    monkeypatch.setattr(node_launch, "run", launch_timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        pe._portable_node_run(b"x", node=script, timeout=9, check=False)

    real_map = pe._map_node_launch_error

    def map_without_timeout(exc, command, timeout):
        if exc.code == "ELAUNCHFAILURE" and "timed out" in str(exc):
            raise exc
        return real_map(exc, command, timeout)

    monkeypatch.setattr(pe, "_map_node_launch_error", map_without_timeout)
    with pytest.raises(node_launch.LaunchError, match="timed out"):
        pe._portable_node_run(b"x", node=script, timeout=9, check=False)


def test_mutation_stdin_zero_mapping_required(tmp_path, monkeypatch):
    script = _write_fake_node(
        tmp_path,
        version="v24.0.0",
        close_stdin_unread=True,
        stdin_close_exit_code=0,
        stdin_close_stdout=b"z",
    )
    assert pe._portable_node_run(_LARGE_INPUT, node=script, check=False).stdout == b"z"
    real_map = pe._map_node_launch_error

    def map_without_stdin_zero(exc, command, timeout):
        if exc.code == "ELAUNCHSTDINCLOSED" and (exc.status if exc.status is not None else -1) == 0:
            raise exc
        return real_map(exc, command, timeout)

    monkeypatch.setattr(pe, "_map_node_launch_error", map_without_stdin_zero)
    with pytest.raises(pe._node_launch_module().LaunchError, match="stdin pipe closed"):
        pe._portable_node_run(_LARGE_INPUT, node=script, check=False)


_MUTATION_TEST_FILE = Path(__file__).resolve()


def _pytest_on_mutated_portable_execution(
    replacements: list[tuple[str, str]] | None,
    test_name: str,
    *,
    collect_only: bool = False,
) -> int:
    with tempfile.TemporaryDirectory() as directory:
        overlay = Path(directory)
        shutil.copytree(ROOT / "vendor", overlay / "vendor")
        source = (ROOT / "src/mcp_toolcall_lab/portable_execution.py").read_text(encoding="utf-8")
        if replacements is not None:
            for old, new in replacements:
                if source.count(old) != 1:
                    raise AssertionError(f"mutation anchor must appear once: {old!r}")
                source = source.replace(old, new, 1)
        target_dir = overlay / "src/mcp_toolcall_lab"
        target_dir.mkdir(parents=True)
        shutil.copytree(ROOT / "src/mcp_toolcall_lab/static", target_dir / "static")
        shutil.copytree(ROOT / "src/mcp_toolcall_lab/adapters", target_dir / "adapters")
        shutil.copytree(ROOT / "src/mcp_toolcall_lab/mock", target_dir / "mock")
        (target_dir / "__init__.py").write_text("", encoding="utf-8")
        (target_dir / "portable_execution.py").write_text(source, encoding="utf-8")
        isolated = overlay / "isolated"
        isolated.mkdir()
        isolated_test = isolated / "test_portable_execution_node_launch.py"
        shutil.copy2(_MUTATION_TEST_FILE, isolated_test)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(overlay / "src") + os.pathsep + env.get("PYTHONPATH", "")
        node_test_id = f"{isolated_test.name}::{test_name}"
        argv = [sys.executable, "-m", "pytest"]
        if collect_only:
            argv.append("--collect-only")
        argv.extend([node_test_id, "-q", "--tb=no"])
        completed = subprocess.run(argv, cwd=isolated, env=env, check=False)
        return completed.returncode


def _assert_mutation_subprocess_guard(
    replacements: list[tuple[str, str]],
    test_name: str,
) -> None:
    assert _pytest_on_mutated_portable_execution(None, test_name, collect_only=True) == 0
    assert _pytest_on_mutated_portable_execution(None, test_name) == 0
    assert _pytest_on_mutated_portable_execution(replacements, test_name) == 1


def test_mutation_subprocess_broken_timeout_fails_parity_test():
    _assert_mutation_subprocess_guard(
        [
            (
                '    if exc.code == "ELAUNCHFAILURE" and "timed out" in str(exc):',
                '    if False and exc.code == "ELAUNCHFAILURE" and "timed out" in str(exc):',
            )
        ],
        "test_timeout_matches_legacy",
    )


def test_mutation_subprocess_broken_stdin_zero_fails_parity_test():
    _assert_mutation_subprocess_guard(
        [
            (
                '    if exc.code == "ELAUNCHSTDINCLOSED":',
                '    if False and exc.code == "ELAUNCHSTDINCLOSED":',
            )
        ],
        "test_stdin_closed_zero_exit_returns_stdout",
    )


def test_mutation_subprocess_broken_over_cap_mapping_fails_execute_test():
    _assert_mutation_subprocess_guard(
        [
            (
                '        if exc.code == "ERESPONSETOOLARGE":',
                '        if False and exc.code == "ERESPONSETOOLARGE":',
            )
        ],
        "test_receipt_over_cap_matches_legacy_execute_message",
    )


@pytest.mark.parametrize("failure_code", [None, "ELAUNCHFAILURE", "ERESPONSETOOLARGE"])
def test_summary_directory_uses_bounded_launcher_and_preserves_timeout_command(tmp_path, monkeypatch, failure_code):
    script = _write_fake_node(tmp_path, version="v24.0.0", on_eval="pass")
    node_launch = pe._node_launch_module()
    summary_dir = tmp_path / "reviewed summaries"
    arguments = (CORE, "--native-only", "--summary-dir", str(summary_dir))

    def run(*args, **kwargs):
        assert args == arguments
        assert kwargs["input"] == b"source-selection"
        assert kwargs["timeout"] == pe.NODE_BRIDGE_TIMEOUT == 15
        assert kwargs["response_max_bytes"] == pe.NODE_RECEIPT_MAX_BYTES == 4 * 1024 * 1024
        assert kwargs["node"] == str(script)
        assert kwargs["probe"] is False
        assert kwargs["check"] is True
        if failure_code:
            raise node_launch.LaunchError(
                "node_launch: Node process timed out" if failure_code == "ELAUNCHFAILURE" else "too large",
                code=failure_code, stdout=b"partial", stderr=b"diagnostic",
            )
        return subprocess.CompletedProcess([str(script), *map(str, args)], 0, b"{}", b"")

    monkeypatch.setattr(node_launch, "run", run)
    if failure_code == "ELAUNCHFAILURE":
        with pytest.raises(subprocess.TimeoutExpired) as info:
            pe._portable_node_run(b"source-selection", node=script, summary_dir=summary_dir)
        assert info.value.cmd == [str(script), *map(str, arguments)]
        assert info.value.output == b"partial"
        assert info.value.stderr == b"diagnostic"
    elif failure_code == "ERESPONSETOOLARGE":
        with pytest.raises(pe.ReceiptTooLargeError):
            pe._portable_node_run(b"source-selection", node=script, summary_dir=summary_dir)
    else:
        assert pe._portable_node_run(b"source-selection", node=script, summary_dir=summary_dir).stdout == b"{}"
