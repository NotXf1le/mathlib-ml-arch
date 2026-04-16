from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


@dataclass(frozen=True)
class CommandSpec:
    command: list[str]
    cwd: Path
    env: dict[str, str]
    timeout_seconds: int
    include_duration: bool = False


class SubprocessRunner:
    def run(self, spec: CommandSpec) -> dict[str, object]:
        started = time.perf_counter()
        try:
            result = subprocess.run(
                spec.command,
                cwd=spec.cwd,
                env=spec.env,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=spec.timeout_seconds,
            )
        except OSError as exc:
            payload = {
                "command": spec.command,
                "cwd": str(spec.cwd),
                "returncode": None,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "success": False,
                "timed_out": False,
                "timeout_seconds": spec.timeout_seconds,
            }
            if spec.include_duration:
                payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
            return payload
        except subprocess.TimeoutExpired as exc:
            payload: dict[str, object] = {
                "command": spec.command,
                "cwd": str(spec.cwd),
                "returncode": None,
                "stdout": coerce_text(exc.stdout),
                "stderr": (
                    f"{coerce_text(exc.stderr).rstrip()}\n"
                    f"Command timed out after {spec.timeout_seconds} seconds."
                ).strip(),
                "success": False,
                "timed_out": True,
                "timeout_seconds": spec.timeout_seconds,
            }
            if spec.include_duration:
                payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
            return payload

        payload = {
            "command": spec.command,
            "cwd": str(spec.cwd),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.returncode == 0,
            "timed_out": False,
            "timeout_seconds": spec.timeout_seconds,
        }
        if spec.include_duration:
            payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return payload


def detect_tool_version(
    tool: Path | None,
    env_factory: Callable[[Path | None], dict[str, str]],
    runner: SubprocessRunner | None = None,
) -> str | None:
    if tool is None:
        return None

    active_runner = runner or SubprocessRunner()
    result = active_runner.run(
        CommandSpec(
            command=[str(tool), "--version"],
            cwd=tool.parent,
            env=env_factory(tool),
            timeout_seconds=5,
        )
    )
    output = str(result.get("stdout") or result.get("stderr") or "").strip()
    if not output:
        return None
    return output.splitlines()[0]
