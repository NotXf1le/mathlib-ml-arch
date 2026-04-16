from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from script_output import append_unique


@dataclass(frozen=True)
class SetupStep:
    label: str
    script: str
    args: list[str]


@dataclass(frozen=True)
class SetupWorkflowDependencies:
    build_preflight: Callable[[Path, str], dict[str, object]]
    run_step: Callable[[str, Path, list[str], int], dict[str, object]]


class SetupPlanner:
    @staticmethod
    def target_ready(payload: dict[str, object], target: str) -> bool:
        key = "ready_for_verification" if target == "verify" else "ready_for_search"
        return bool(payload.get(key))

    @staticmethod
    def readiness_level(payload: dict[str, object]) -> str:
        if bool(payload.get("ready_for_verification")):
            return "verification-ready"
        if bool(payload.get("ready_for_search")):
            return "search-ready"
        return "incomplete"

    @staticmethod
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

    @staticmethod
    def planned_steps(payload: dict[str, object], target: str, timeout_seconds: int) -> list[SetupStep]:
        steps: list[SetupStep] = []
        needs_search_workspace = not bool(payload.get("ready_for_search"))
        needs_verify_workspace = target == "verify" and not bool(payload.get("ready_for_verification"))
        needs_toolchain = (
            (needs_search_workspace and payload.get("lake_path") is None)
            or (target == "verify" and payload.get("lean_path") is None)
        )

        if needs_toolchain:
            steps.append(
                SetupStep(
                    label="Bootstrap Lean toolchain",
                    script="bootstrap_toolchain.py",
                    args=["--timeout-seconds", str(timeout_seconds)],
                )
            )

        if target == "search" and needs_search_workspace:
            steps.append(
                SetupStep(
                    label="Prepare shared search workspace",
                    script="bootstrap_proofs.py",
                    args=["--target", "search", "--skip-verify", "--timeout-seconds", str(timeout_seconds)],
                )
            )
        elif target == "verify" and needs_verify_workspace:
            steps.append(
                SetupStep(
                    label="Prepare shared verification workspace",
                    script="bootstrap_proofs.py",
                    args=["--target", "verify", "--timeout-seconds", str(timeout_seconds)],
                )
            )

        return steps


class SetupWorkflow:
    def __init__(self, dependencies: SetupWorkflowDependencies) -> None:
        self._deps = dependencies
        self._planner = SetupPlanner()

    def execute(self, args: object, workspace_root: Path) -> tuple[dict[str, object], int]:
        target = str(getattr(args, "target"))
        timeout_seconds = int(getattr(args, "timeout_seconds"))
        preflight = self._deps.build_preflight(workspace_root, "shared")
        before_readiness = str(preflight.get("readiness_level") or self._planner.readiness_level(preflight))
        payload: dict[str, object] = {
            "requested_workspace": str(workspace_root),
            "target": target,
            "check_only": bool(getattr(args, "check_only")),
            "auto_confirmed": bool(getattr(args, "yes")),
            "preflight": preflight,
            "readiness_before": before_readiness,
            "readiness_after": before_readiness,
            "missing_requirements": self._planner.missing_requirements(preflight, target),
            "planned_steps": [],
            "steps": [],
            "next_steps": list(preflight.get("next_steps", [])),
            "success": False,
            "status": "failure",
        }

        if self._planner.target_ready(preflight, target):
            payload["success"] = True
            payload["status"] = "success"
            return payload, 0

        steps = self._planner.planned_steps(preflight, target, timeout_seconds)
        payload["planned_steps"] = [step.label for step in steps]

        if bool(getattr(args, "check_only")):
            payload["status"] = "needs_setup"
            return payload, 1

        if not bool(preflight.get("shared_workspace_writable")):
            payload["status"] = "blocked"
            return payload, 3

        if not steps:
            payload["status"] = "blocked"
            return payload, 3

        if bool(getattr(args, "json")) and not bool(getattr(args, "yes")):
            payload["status"] = "needs_confirmation"
            payload["next_steps"] = [
                f"Rerun `python scripts/setup_plugin.py --target {target} --yes` when you want to apply setup changes."
            ]
            return payload, 4

        if not bool(getattr(args, "yes")):
            return payload, -1

        for step in steps:
            step_payload = self._deps.run_step(step.script, workspace_root, [str(arg) for arg in step.args], timeout_seconds)
            payload["steps"].append(
                {
                    "label": step.label,
                    "script": step.script,
                    "success": bool(step_payload.get("success")),
                    "status": step_payload.get("status"),
                    "exit_code": step_payload.get("exit_code"),
                    "payload": step_payload,
                }
            )
            current = self._deps.build_preflight(workspace_root, "shared")
            payload["readiness_after"] = str(current.get("readiness_level") or self._planner.readiness_level(current))
            if not bool(step_payload.get("success")) and not self._planner.target_ready(current, target):
                payload["next_steps"] = list(current.get("next_steps", []))
                for next_step in step_payload.get("next_steps", []):
                    append_unique(payload["next_steps"], str(next_step))
                payload["status"] = "partial_success" if bool(current.get("ready_for_search")) else "failure"
                payload["success"] = False
                return payload, 5

        final_preflight = self._deps.build_preflight(workspace_root, "shared")
        payload["preflight_after"] = final_preflight
        payload["readiness_after"] = str(final_preflight.get("readiness_level") or self._planner.readiness_level(final_preflight))
        payload["next_steps"] = list(final_preflight.get("next_steps", []))
        payload["success"] = self._planner.target_ready(final_preflight, target)
        if payload["success"]:
            payload["status"] = "success"
            return payload, 0
        if bool(final_preflight.get("ready_for_search")):
            payload["status"] = "partial_success"
            return payload, 5
        payload["status"] = "failure"
        return payload, 5
