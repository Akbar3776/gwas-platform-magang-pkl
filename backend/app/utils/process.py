"""Safe subprocess helpers for long-running bioinformatics CLI tools."""

from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

logger = logging.getLogger(__name__)
LineCallback = Callable[[str], None]


@dataclass(frozen=True)
class CLIExecutionResult:
    command: tuple[str, ...]
    returncode: int
    log_file: Path


class CLIExecutionError(RuntimeError):
    """Base error for a failed or timed-out CLI process."""

    def __init__(
        self,
        cmd: Sequence[str],
        message: str,
        *,
        returncode: int | None,
        log_file: Path,
        last_lines: Sequence[str] = (),
        timed_out: bool = False,
    ) -> None:
        self.cmd = tuple(str(part) for part in cmd)
        self.returncode = returncode
        self.log_file = log_file
        self.last_lines = tuple(last_lines)
        self.timed_out = timed_out
        super().__init__(message)


class SubprocessFailed(CLIExecutionError):
    """Raised when a CLI exits with a non-zero return code."""

    def __init__(
        self,
        cmd: Sequence[str],
        returncode: int,
        log_file: Path,
        last_lines: Sequence[str] = (),
    ) -> None:
        tail = "".join(last_lines).strip()
        detail = f" Pesan terakhir: {tail}" if tail else ""
        super().__init__(
            cmd,
            f"Command gagal (exit code {returncode}): {' '.join(map(str, cmd))}.{detail} "
            f"Log lengkap: {log_file}",
            returncode=returncode,
            log_file=log_file,
            last_lines=last_lines,
        )


class CLITimeoutError(CLIExecutionError):
    """Raised when a CLI exceeds its wall-clock timeout."""

    def __init__(
        self,
        cmd: Sequence[str],
        timeout: float,
        log_file: Path,
        last_lines: Sequence[str] = (),
    ) -> None:
        tail = "".join(last_lines).strip()
        detail = f" Pesan terakhir: {tail}" if tail else ""
        super().__init__(
            cmd,
            f"Command melewati timeout {timeout:g} detik: {' '.join(map(str, cmd))}.{detail} "
            f"Log lengkap: {log_file}",
            returncode=None,
            log_file=log_file,
            last_lines=last_lines,
            timed_out=True,
        )


def _reader(stream, output_queue: queue.Queue[tuple[str, str | None]]) -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put(("line", line))
    finally:
        stream.close()
        output_queue.put(("eof", None))


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Terminate the process group so child tools do not survive a timeout."""
    if process.poll() is not None:
        return

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
        process.wait()


def run_cli_command(
    command: Sequence[str],
    *,
    cwd: Path | str | None = None,
    log_file: Path | str,
    timeout: float | None = None,
    on_line: LineCallback | None = None,
    env: Mapping[str, str] | None = None,
    tail_lines: int = 40,
    preexec_fn: Callable[[], None] | None = None,
) -> CLIExecutionResult:
    """Run a CLI command while streaming output to a log file.

    The command must be an argument list, never a shell string. Output is
    written line-by-line and only the last ``tail_lines`` lines are kept in
    memory for useful failure messages. ``timeout`` is a wall-clock limit;
    both the process and its descendants are terminated when it expires.
    """
    if not command or not all(isinstance(part, str) for part in command):
        raise ValueError("command harus berupa list argumen string yang tidak kosong")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout harus lebih besar dari 0")

    command = tuple(command)
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(cwd) if cwd is not None else None
    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)

    process_group_kwargs = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    merged_env = {**os.environ, **env} if env is not None else None

    logger.info("Menjalankan command: %s (cwd=%s)", " ".join(command), work_dir or Path.cwd())
    output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    recent_lines: deque[str] = deque(maxlen=max(tail_lines, 1))
    started_at = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=str(work_dir) if work_dir is not None else None,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        preexec_fn=preexec_fn,
        **process_group_kwargs,
    )
    assert process.stdout is not None
    reader = threading.Thread(target=_reader, args=(process.stdout, output_queue), daemon=True)
    reader.start()

    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        log.write(f"\n$ {' '.join(command)}\n")
        stream_closed = False
        while not stream_closed or process.poll() is None:
            if timeout is not None and time.monotonic() - started_at > timeout:
                _terminate_process(process)
                while True:
                    try:
                        kind, line = output_queue.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "line" and line is not None:
                        log.write(line)
                        recent_lines.append(line)
                        if on_line is not None:
                            on_line(line.rstrip("\r\n"))
                log.write(f"\n[TIMEOUT] Proses dihentikan setelah {timeout:g} detik\n")
                raise CLITimeoutError(command, timeout, log_path, tuple(recent_lines))

            try:
                kind, line = output_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if kind == "eof":
                stream_closed = True
                continue
            assert line is not None
            log.write(line)
            recent_lines.append(line)
            if on_line is not None:
                on_line(line.rstrip("\r\n"))

        process.wait()

    result = CLIExecutionResult(command, process.returncode, log_path)
    if process.returncode != 0:
        raise SubprocessFailed(command, process.returncode, log_path, tuple(recent_lines))
    return result


def run_subprocess(
    cmd: list[str],
    cwd: Path,
    log_file: Path,
    timeout: int | float | None = None,
) -> None:
    """Backward-compatible adapter used by existing pipeline tasks."""
    run_cli_command(cmd, cwd=cwd, log_file=log_file, timeout=timeout)
