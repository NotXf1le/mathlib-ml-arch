from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import doctor
from common import configure_stdout, requested_workspace_root


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
    if message and message not in items:
        items.append(message)


def target_ready(payload: dict[str, object], target: str) -> bool:
    key = "ready_for_verification" if target == "verify" else "ready_for_search"
    return bool(payload.get(key))


def readiness_level(payload: dict[str, object]) -> str:
    if bool(payload.get("ready_for_verification")):
        return "verification-ready"
    if bool(payload.get("ready_for_search")):
        return "search-ready"
    return "incomplete"


def render_progress(index: int, total: int) -> str:
    width = 28
    completed = 0 if total <= 0 else round((index / total) * width)
    bar = "#" * completed + "-" * (width - completed)
    percent = 0 if total <= 0 else round((index / total) * 100)
    return f"[{bar}] {percent:>3d}%"


def missing_requirements(payload: dict[str, object], target: str) -> list[str]:
    missing: list[str] = []
    if not bool(payload.get("shared_workspace_writable")):
        append_unique(missing, "shared CODEX_HOME workspace is not writable")
    if payload.get("lake_path") is None:
        append_unique(missing, "lake is unavailable to the plugin")
    if target == "verify" and payload.get("lean_path") is None:
        append_unique(missing, "lean is unavailable to the plugin")
    if not bool(payload.get("proofs_exists")):
        append_unique(missing, "shared proofs workspace is missing")
    if not bool(payload.get("mathlib_source_exists")):
        append_unique(missing, "mathlib sources are missing")
    if bool(payload.get("proofs_exists")) and bool(payload.get("mathlib_source_exists")) and not bool(payload.get("toolchain_compatible")):
        append_unique(missing, "shared mathlib checkout does not match the project toolchain")
    if target == "verify" and int(payload.get("package_library_path_count", 0)) == 0:
        append_unique(missing, "compiled package libraries are missing")
    if target == "verify" and not bool(payload.get("mathlib_artifact_exists")):
        append_unique(missing, "Mathlib.olean is missing")
    if (
        target == "verify"
        and bool(payload.get("verification_smoke_checked"))
        and payload.get("verification_smoke_success") is False
    ):
        append_unique(missing, "the shared Mathlib import smoke check is failing")
    return missing


def planned_steps(payload: dict[str, object], target: str, timeout_seconds: int) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    needs_search_workspace = not bool(payload.get("ready_for_search"))
    needs_verify_workspace = target == "verify" and not bool(payload.get("ready_for_verification"))
    needs_toolchain = (
        (needs_search_workspace and payload.get("lake_path") is None)
        or (target == "verify" and payload.get("lean_path") is None)
    )

    if needs_toolchain:
        steps.append(
            {
                "label": "Bootstrap Lean toolchain",
                "script": "bootstrap_toolchain.py",
                "args": ["--timeout-seconds", str(timeout_seconds)],
            }
        )

    if target == "search" and needs_search_workspace:
        steps.append(
            {
                "label": "Prepare shared search workspace",
                "script": "bootstrap_proofs.py",
                "args": ["--target", "search", "--skip-verify", "--timeout-seconds", str(timeout_seconds)],
            }
        )
    elif target == "verify" and needs_verify_workspace:
        steps.append(
            {
                "label": "Prepare shared verification workspace",
                "script": "bootstrap_proofs.py",
                "args": ["--target", "verify", "--timeout-seconds", str(timeout_seconds)],
            }
        )

    return steps


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
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(payload)


def main() -> int:
    configure_stdout()
    args = parse_args()
    workspace_root = requested_workspace_root(args.workspace)
    preflight = doctor.build_payload(workspace_root, "shared")
    before_readiness = str(preflight.get("readiness_level") or readiness_level(preflight))
    payload: dict[str, object] = {
        "requested_workspace": str(workspace_root),
        "target": args.target,
        "check_only": args.check_only,
        "auto_confirmed": args.yes,
        "preflight": preflight,
        "readiness_before": before_readiness,
        "readiness_after": before_readiness,
        "missing_requirements": missing_requirements(preflight, args.target),
        "planned_steps": [],
        "steps": [],
        "next_steps": list(preflight.get("next_steps", [])),
        "success": False,
        "status": "failure",
    }

    if target_ready(preflight, args.target):
        payload["success"] = True
        payload["status"] = "success"
        emit_payload(args, payload)
        return 0

    steps = planned_steps(preflight, args.target, args.timeout_seconds)
    payload["planned_steps"] = [str(step["label"]) for step in steps]

    if args.check_only:
        payload["status"] = "needs_setup"
        emit_payload(args, payload)
        return 1

    if not bool(preflight.get("shared_workspace_writable")):
        payload["status"] = "blocked"
        emit_payload(args, payload)
        return 3

    if not steps:
        payload["status"] = "blocked"
        emit_payload(args, payload)
        return 3

    if args.json and not args.yes:
        payload["status"] = "needs_confirmation"
        payload["next_steps"] = [
            f"Rerun `python scripts/setup_plugin.py --target {args.target} --yes` when you want to apply setup changes."
        ]
        emit_payload(args, payload)
        return 4

    if not args.yes:
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

    total_phases = len(steps) + 1
    if args.yes and not args.json:
        print(f"{render_progress(1, total_phases)} Preflight complete")

    for index, step in enumerate(steps, start=2):
        if not args.json:
            print(f"{render_progress(index, total_phases)} {step['label']}")
        step_payload = run_json_step(
            str(step["script"]),
            workspace_root,
            [str(arg) for arg in step["args"]],
            timeout_seconds=args.timeout_seconds,
        )
        payload["steps"].append(
            {
                "label": str(step["label"]),
                "script": str(step["script"]),
                "success": bool(step_payload.get("success")),
                "status": step_payload.get("status"),
                "exit_code": step_payload.get("exit_code"),
                "payload": step_payload,
            }
        )
        current = doctor.build_payload(workspace_root, "shared")
        payload["readiness_after"] = str(current.get("readiness_level") or readiness_level(current))
        if not bool(step_payload.get("success")) and not target_ready(current, args.target):
            payload["next_steps"] = list(current.get("next_steps", []))
            for next_step in step_payload.get("next_steps", []):
                append_unique(payload["next_steps"], str(next_step))
            payload["status"] = "partial_success" if bool(current.get("ready_for_search")) else "failure"
            payload["success"] = False
            emit_payload(args, payload)
            return 5

    final_preflight = doctor.build_payload(workspace_root, "shared")
    payload["preflight_after"] = final_preflight
    payload["readiness_after"] = str(final_preflight.get("readiness_level") or readiness_level(final_preflight))
    payload["next_steps"] = list(final_preflight.get("next_steps", []))
    payload["success"] = target_ready(final_preflight, args.target)
    if payload["success"]:
        payload["status"] = "success"
        return_code = 0
    elif bool(final_preflight.get("ready_for_search")):
        payload["status"] = "partial_success"
        return_code = 5
    else:
        payload["status"] = "failure"
        return_code = 5

    emit_payload(args, payload)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
