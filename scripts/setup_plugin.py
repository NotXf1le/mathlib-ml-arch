from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import doctor
from common import configure_stdout, requested_workspace_root
from script_output import PayloadEmitter, append_unique as append_unique_message
from setup_workflow import SetupPlanner, SetupWorkflow, SetupWorkflowDependencies


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether the plugin is ready, then guide the user through shared mathlib setup for search or Lean verification."
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root or child directory to inspect. Defaults to the current directory.",
    )
    parser.add_argument(
        "--target",
        choices=["search", "verify"],
        default="search",
        help="Desired readiness level. `search` prepares theorem search, `verify` additionally prepares Lean verification artifacts.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only inspect readiness and print the recommended next action.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run the required setup steps without interactive confirmation.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Per-command timeout for child setup steps.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )
    return parser.parse_args()


def append_unique(items: list[str], message: str | None) -> None:
    append_unique_message(items, message)


def target_ready(payload: dict[str, object], target: str) -> bool:
    return SetupPlanner.target_ready(payload, target)


def readiness_level(payload: dict[str, object]) -> str:
    return SetupPlanner.readiness_level(payload)


def render_progress(index: int, total: int) -> str:
    width = 28
    completed = 0 if total <= 0 else round((index / total) * width)
    bar = "#" * completed + "-" * (width - completed)
    percent = 0 if total <= 0 else round((index / total) * 100)
    return f"[{bar}] {percent:>3d}%"


def missing_requirements(payload: dict[str, object], target: str) -> list[str]:
    return SetupPlanner.missing_requirements(payload, target)


def planned_steps(payload: dict[str, object], target: str, timeout_seconds: int) -> list[dict[str, object]]:
    return [
        {"label": step.label, "script": step.script, "args": list(step.args)}
        for step in SetupPlanner.planned_steps(payload, target, timeout_seconds)
    ]


def step_command(script_name: str, workspace_root: Path, args: list[str]) -> list[str]:
    script_path = Path(__file__).with_name(script_name)
    command = [sys.executable, str(script_path)]
    if script_name == "bootstrap_proofs.py":
        command.extend(["--workspace", str(workspace_root), "--scope", "shared"])
    command.extend(args)
    command.append("--json")
    return command


def run_json_step(script_name: str, workspace_root: Path, args: list[str], timeout_seconds: int) -> dict[str, object]:
    command = step_command(script_name, workspace_root, args)
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds + 30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        return {
            "success": False,
            "status": "failure",
            "error": f"{script_name} timed out after {timeout_seconds + 30} seconds.",
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": None,
            "command": command,
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {
            "success": False,
            "status": "failure",
            "error": f"{script_name} returned unreadable output.",
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    payload["exit_code"] = result.returncode
    payload["command"] = command
    return payload


def confirm_setup() -> bool:
    try:
        answer = input("Run setup now? [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def print_human(payload: dict[str, object]) -> None:
    print(f"status: {payload['status']}")
    print(f"target: {payload['target']}")
    print(f"requested workspace: {payload['requested_workspace']}")
    print(f"readiness before: {payload['readiness_before']}")
    print(f"readiness after: {payload['readiness_after']}")
    if payload["missing_requirements"]:
        print("missing:")
        for item in payload["missing_requirements"]:
            print(f"  - {item}")
    if payload["steps"]:
        print("steps:")
        for step in payload["steps"]:
            outcome = "ok" if step.get("success") else "failed"
            print(f"  - {step['label']}: {outcome}")
            if step.get("status"):
                print(f"    status: {step['status']}")
    if payload["next_steps"]:
        print("next steps:")
        for step in payload["next_steps"]:
            print(f"  - {step}")


def emit_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    PayloadEmitter(json_enabled=args.json, human_printer=print_human).emit(payload)


def _workflow() -> SetupWorkflow:
    return SetupWorkflow(
        SetupWorkflowDependencies(
            build_preflight=doctor.build_payload,
            run_step=run_json_step,
        )
    )


def main() -> int:
    configure_stdout()
    args = parse_args()
    workspace_root = requested_workspace_root(args.workspace)
    workflow = _workflow()
    payload, return_code = workflow.execute(args, workspace_root)

    if return_code == -1:
        steps = planned_steps(payload["preflight"], args.target, args.timeout_seconds)
        print(f"{render_progress(1, len(steps) + 1)} Preflight complete")
        if payload["missing_requirements"]:
            print("Setup will address:")
            for item in payload["missing_requirements"]:
                print(f"  - {item}")
        if not confirm_setup():
            payload["status"] = "cancelled"
            payload["next_steps"] = [
                f"Run `python scripts/setup_plugin.py --target {args.target} --yes` when you want to download and configure the shared environment."
            ]
            emit_payload(args, payload)
            return 4
        args.yes = True
        payload, return_code = workflow.execute(args, workspace_root)

    emit_payload(args, payload)
    return int(return_code)


if __name__ == "__main__":
    raise SystemExit(main())
