from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from common import (
    add_git_safe_directories,
    codex_home,
    configure_stdout,
    default_project_name,
    discover_package_lib_dirs,
    find_existing_proofs_root,
    find_lake,
    git_safe_directories_for_proofs,
    is_shared_workspace,
    requested_workspace_root,
    shared_workspace_root,
    subprocess_env_for_tool,
    writability_error,
)


DEFAULT_SCRATCH = """import Mathlib

#check True.intro
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap the shared proofs/ project, fetch mathlib, and validate the environment."
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root or child directory. Defaults to the current directory.",
    )
    parser.add_argument(
        "--scope",
        choices=["auto", "local", "shared"],
        default="auto",
        help="Where to create or reuse the proofs project. The plugin is shared-workspace-only; `auto`, `shared`, and legacy `local` all resolve to the shared CODEX_HOME cache.",
    )
    parser.add_argument(
        "--proofs-dir",
        default="proofs",
        help="Proof project directory relative to the workspace root.",
    )
    parser.add_argument(
        "--name",
        help="Lean project name used for `lake init`. Defaults to <WorkspaceName>Proofs.",
    )
    parser.add_argument(
        "--skip-update",
        action="store_true",
        help="Do not run `lake update` even if mathlib sources are missing.",
    )
    parser.add_argument(
        "--skip-cache",
        action="store_true",
        help="Do not run `lake exe cache get` even if compiled libraries are missing.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip the final `lean_check.py` smoke test.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Per-command timeout for bootstrap and verification subprocesses.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON diagnostics.",
    )
    return parser.parse_args()


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def append_unique(items: list[str], message: str | None) -> None:
    if message and message not in items:
        items.append(message)


def emit_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(payload)


def run_command(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.returncode == 0,
            "timed_out": False,
            "timeout_seconds": timeout_seconds,
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": None,
            "stdout": _coerce_text(exc.stdout),
            "stderr": (
                f"{_coerce_text(exc.stderr).rstrip()}\nCommand timed out after {timeout_seconds} seconds."
            ).strip(),
            "success": False,
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
            "duration_ms": duration_ms,
        }


def classify_step_failure(step: dict[str, object]) -> str:
    if step.get("success"):
        return "ok"

    combined = f"{step.get('stderr', '')}\n{step.get('stdout', '')}".lower()
    if step.get("timed_out"):
        return "timeout"
    if "could not create home directory" in combined or ("create home directory" in combined and ".elan" in combined):
        return "home_directory"
    if any(token in combined for token in ("permission denied", "permissionerror", "access is denied", "operation not permitted")):
        return "permissions"
    if "cache-prune" in combined or "cache prune" in combined:
        return "cache_prune"
    if any(
        token in combined
        for token in (
            "could not resolve host",
            "connection reset",
            "connection timed out",
            "download",
            "failed to connect",
            "network",
            "tls",
            "curl' exited with code 7",
            "reservoir lookup failed",
            "failed sending request",
        )
    ):
        return "network"
    if "no default toolchain" in combined or ("elan" in combined and "toolchain" in combined):
        return "toolchain"
    if any(token in combined for token in ("manifest", "mathlib package", "package configuration")):
        return "package_state"
    return "unknown"


def failure_guidance(step_name: str, reason: str, selected_scope: str) -> str:
    if reason == "permissions":
        if selected_scope == "shared":
            return (
                f"`{step_name}` could not write into the shared workspace. "
                "Set `CODEX_HOME` to a writable directory and rerun bootstrap."
            )
        return (
            f"`{step_name}` could not write into the selected workspace. "
            "Choose a writable workspace or rerun after setting `CODEX_HOME` to a writable directory."
        )
    if reason == "home_directory":
        return (
            f"`{step_name}` could not create Lean's home directory. "
            "Set `CODEX_HOME` to a writable directory or rerun in an environment where HOME/USERPROFILE is writable."
        )
    if reason == "timeout":
        return (
            f"`{step_name}` timed out. "
            "Rerun with a larger `--timeout-seconds`, or continue with `--skip-update` / `--skip-cache` if the required artifacts are already present."
        )
    if reason == "network":
        return (
            f"`{step_name}` could not finish because package download or network access failed. "
            "Retry when network access is available."
        )
    if reason == "toolchain":
        return (
            f"`{step_name}` could not use the Lean toolchain. "
            "Run `python scripts/doctor.py` to inspect the environment, then reinstall or initialize Lean 4 before retrying."
        )
    if reason == "package_state":
        return (
            f"`{step_name}` reported a package-state error. "
            "Inspect the package manifest under `proofs/.lake`, then rerun `lake update` once the workspace is consistent."
        )
    if reason == "cache_prune":
        return (
            f"`{step_name}` reported a cache-prune cleanup failure. "
            "If mathlib sources and compiled libraries are present, treat this as a warning; otherwise rerun once the package cache is healthy."
        )
    return f"`{step_name}` failed. Inspect its stderr and rerun bootstrap once the underlying issue is fixed."


def select_bootstrap_workspace(
    requested_workspace: Path,
    scope: str,
) -> tuple[Path, str, list[str]]:
    warnings: list[str] = []
    if scope == "local":
        warnings.append(
            "Local proofs workspaces are no longer supported. `--scope local` is treated as shared-workspace mode."
        )
    return shared_workspace_root(), "shared", warnings


def ensure_scratch_file(path: Path) -> None:
    if path.exists():
        return
    path.write_text(DEFAULT_SCRATCH, encoding="utf-8")


def partial_project_guidance(proofs_dir: Path) -> str:
    if (proofs_dir / "lakefile.toml").exists() or (proofs_dir / "lake-manifest.json").exists():
        return (
            "A previous `lake init` left a partial proofs project here. Remove the incomplete `proofs/` directory or clean the shared cache entry before retrying bootstrap."
        )
    return "The proofs/ directory already exists but is not a Lean project. Empty it or initialize it manually."


def print_human(payload: dict[str, object]) -> None:
    print(f"status: {payload.get('status', 'failure')}")
    print(f"requested workspace: {payload['requested_workspace']}")
    print(f"selected workspace: {payload['workspace_root']} ({payload['selected_scope']})")
    print(f"proofs: {payload['proofs_dir']}")
    environment = payload.get("environment", {})
    if environment:
        print(f"codex home: {environment.get('codex_home')}")
        print(f"tool HOME: {environment.get('home') or 'unset'}")
        print(f"tool ELAN_HOME: {environment.get('elan_home') or 'unset'}")
    print(f"project initialized: {payload['project_initialized']}")
    print(f"scratch file present: {payload['proof_scratch_exists']}")
    for step in payload["steps"]:
        if step["success"]:
            outcome = "ok"
        elif step.get("non_fatal"):
            outcome = f"warning ({step['reason']})"
        else:
            outcome = f"failed ({step['reason']})"
        print(f"{step['name']}: {outcome}")
        stderr = str(step.get("stderr", "")).strip()
        if stderr:
            print(f"  stderr: {stderr.splitlines()[-1]}")
    if payload.get("verification"):
        verification = payload["verification"]
        status = "ok" if verification.get("success") else "failed"
        print(f"verification: {status}")
        if verification.get("verification_method"):
            print(f"  method: {verification['verification_method']}")
        if verification.get("timed_out"):
            print(f"  timeout: {verification['timeout_seconds']} seconds")
    if payload["warnings"]:
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"  - {warning}")
    if payload["next_steps"]:
        print("next steps:")
        for step in payload["next_steps"]:
            print(f"  - {step}")


def main() -> int:
    configure_stdout()
    args = parse_args()
    requested_workspace = requested_workspace_root(args.workspace)
    local_workspace = find_existing_proofs_root(requested_workspace)
    workspace_root, selected_scope, selection_warnings = select_bootstrap_workspace(requested_workspace, args.scope)
    ignored_local_workspace = (
        local_workspace
        if local_workspace is not None and not is_shared_workspace(local_workspace)
        else None
    )

    proofs_dir = (workspace_root / args.proofs_dir).resolve()
    project_name = args.name or default_project_name(workspace_root)
    lake = find_lake()
    shared_root = shared_workspace_root()
    shared_write_error = writability_error(shared_root)
    selected_write_error = writability_error(workspace_root)

    payload: dict[str, object] = {
        "requested_workspace": str(requested_workspace),
        "workspace_root": str(workspace_root),
        "selected_scope": selected_scope,
        "local_workspace_root": str(local_workspace) if local_workspace else None,
        "ignored_local_workspace_root": str(ignored_local_workspace) if ignored_local_workspace else None,
        "shared_workspace_root": str(shared_root),
        "shared_workspace_writable": shared_write_error is None,
        "shared_workspace_write_error": shared_write_error,
        "selected_workspace_writable": selected_write_error is None,
        "selected_workspace_write_error": selected_write_error,
        "proofs_dir": str(proofs_dir),
        "project_initialized": False,
        "proof_scratch_exists": False,
        "steps": [],
        "verification": None,
        "warnings": list(selection_warnings),
        "next_steps": [],
        "postconditions": {},
        "environment": {
            "codex_home": str(codex_home()),
            "home": None,
            "userprofile": None,
            "elan_home": None,
        },
        "success": False,
        "status": "failure",
    }

    if ignored_local_workspace is not None:
        append_unique(
            payload["warnings"],
            f"Repo-local proofs at {ignored_local_workspace} are ignored in shared-workspace mode.",
        )

    if lake is None:
        append_unique(
            payload["next_steps"],
            "Run `python scripts/bootstrap_toolchain.py` to populate the plugin-local Lean toolchain cache, then rerun bootstrap_proofs.py.",
        )
        emit_payload(args, payload)
        return 2

    env = subprocess_env_for_tool(lake)
    add_git_safe_directories(env, git_safe_directories_for_proofs(proofs_dir))
    payload["environment"]["home"] = env.get("HOME")
    payload["environment"]["userprofile"] = env.get("USERPROFILE")
    payload["environment"]["elan_home"] = env.get("ELAN_HOME")

    project_ready = (proofs_dir / "lean-toolchain").exists() and (proofs_dir / "lakefile.toml").exists()

    if not project_ready:
        if proofs_dir.exists() and any(proofs_dir.iterdir()):
            append_unique(
                payload["next_steps"],
                partial_project_guidance(proofs_dir),
            )
            emit_payload(args, payload)
            return 3

        try:
            proofs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            payload["error"] = f"{type(exc).__name__}: {exc}"
            append_unique(
                payload["next_steps"],
                f"The proofs workspace is not writable: {proofs_dir.parent}.",
            )
            append_unique(
                payload["next_steps"],
                failure_guidance("workspace setup", "permissions", selected_scope),
            )
            emit_payload(args, payload)
            return 3

        init_step = run_command(
            [str(lake), "init", project_name, "math.toml"],
            cwd=proofs_dir,
            env=env,
            timeout_seconds=args.timeout_seconds,
        )
        init_step["name"] = "lake init"
        init_step["reason"] = classify_step_failure(init_step)
        init_step["guidance"] = failure_guidance("lake init", init_step["reason"], selected_scope)
        payload["steps"].append(init_step)
        if not init_step["success"]:
            payload["error"] = f"`lake init` failed: {init_step['reason']}"
            append_unique(payload["next_steps"], init_step["guidance"])
            if proofs_dir.exists() and any(proofs_dir.iterdir()):
                append_unique(payload["next_steps"], partial_project_guidance(proofs_dir))
            emit_payload(args, payload)
            return 4
        project_ready = True

    payload["project_initialized"] = project_ready

    scratch_path = proofs_dir / "ProofScratch.lean"
    try:
        ensure_scratch_file(scratch_path)
    except OSError as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        append_unique(
            payload["next_steps"],
            "Could not create `proofs/ProofScratch.lean`. Ensure the proofs workspace is writable and rerun bootstrap.",
        )
        emit_payload(args, payload)
        return 6
    payload["proof_scratch_exists"] = scratch_path.exists()

    mathlib_source = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
    if not args.skip_update and not mathlib_source.exists():
        update_step = run_command(
            [str(lake), "update"],
            cwd=proofs_dir,
            env=env,
            timeout_seconds=args.timeout_seconds,
        )
        update_step["name"] = "lake update"
        update_step["reason"] = classify_step_failure(update_step)
        update_step["guidance"] = failure_guidance("lake update", update_step["reason"], selected_scope)
        payload["steps"].append(update_step)
        if not update_step["success"]:
            if mathlib_source.exists():
                update_step["non_fatal"] = True
                payload["warnings"].append(
                    f"`lake update` reported {update_step['reason']} but mathlib sources are present, so bootstrap continued."
                )
            else:
                append_unique(payload["next_steps"], update_step["guidance"])

    if not args.skip_cache and len(discover_package_lib_dirs(proofs_dir)) == 0:
        cache_step = run_command(
            [str(lake), "exe", "cache", "get"],
            cwd=proofs_dir,
            env=env,
            timeout_seconds=args.timeout_seconds,
        )
        cache_step["name"] = "lake exe cache get"
        cache_step["reason"] = classify_step_failure(cache_step)
        cache_step["guidance"] = failure_guidance("lake exe cache get", cache_step["reason"], selected_scope)
        payload["steps"].append(cache_step)
        if not cache_step["success"]:
            if len(discover_package_lib_dirs(proofs_dir)) > 0:
                cache_step["non_fatal"] = True
                payload["warnings"].append(
                    f"`lake exe cache get` reported {cache_step['reason']} but compiled libraries are present, so bootstrap continued."
                )
            else:
                append_unique(payload["next_steps"], cache_step["guidance"])

    if not args.skip_verify:
        lean_check = Path(__file__).with_name("lean_check.py")
        try:
            verify_proc = subprocess.run(
                [
                    sys.executable,
                    str(lean_check),
                    "--workspace",
                    str(workspace_root),
                    "--timeout-seconds",
                    str(args.timeout_seconds),
                    "--json",
                ],
                cwd=proofs_dir,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout_seconds + 5,
                env=env,
            )
            try:
                payload["verification"] = json.loads(verify_proc.stdout or "{}")
            except json.JSONDecodeError:
                payload["verification"] = {
                    "success": False,
                    "stdout": verify_proc.stdout,
                    "stderr": verify_proc.stderr,
                }
        except subprocess.TimeoutExpired:
            payload["verification"] = {
                "success": False,
                "timed_out": True,
                "timeout_seconds": args.timeout_seconds + 5,
                "stderr": f"Verification timed out after {args.timeout_seconds + 5} seconds.",
            }

        if not payload["verification"].get("success"):
            append_unique(
                payload["next_steps"],
                "Verification did not pass. Inspect the JSON diagnostics from `python scripts/lean_check.py --json` and retry.",
            )

    package_lib_dirs = discover_package_lib_dirs(proofs_dir)
    verification_success = bool(payload["verification"].get("success")) if payload["verification"] else args.skip_verify
    payload["postconditions"] = {
        "mathlib_source_exists": mathlib_source.exists(),
        "package_library_path_count": len(package_lib_dirs),
        "verification_success": verification_success if not args.skip_verify else None,
    }

    if not args.skip_update and not payload["postconditions"]["mathlib_source_exists"]:
        append_unique(
            payload["next_steps"],
            "Mathlib sources are still missing under `proofs/.lake/packages/mathlib`. Run `lake update` again once the environment issue is fixed.",
        )
    if not args.skip_cache and payload["postconditions"]["package_library_path_count"] == 0:
        append_unique(
            payload["next_steps"],
            "Compiled package libraries are still missing. Run `lake exe cache get` or build the proofs project after `lake update` succeeds.",
        )

    payload["success"] = (
        payload["project_initialized"]
        and payload["proof_scratch_exists"]
        and (args.skip_update or payload["postconditions"]["mathlib_source_exists"])
        and (args.skip_cache or payload["postconditions"]["package_library_path_count"] > 0)
        and (args.skip_verify or bool(payload["postconditions"]["verification_success"]))
    )

    if payload["success"]:
        payload["status"] = "success"
    elif (
        payload["project_initialized"]
        or payload["proof_scratch_exists"]
        or payload["postconditions"]["mathlib_source_exists"]
        or payload["postconditions"]["package_library_path_count"] > 0
    ):
        payload["status"] = "partial_success"

    emit_payload(args, payload)
    return 0 if payload["success"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
