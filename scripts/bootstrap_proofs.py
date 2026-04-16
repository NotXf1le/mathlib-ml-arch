from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
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
    mathlib_module_artifact,
    mathlib_revision_for_toolchain,
    normalize_lean_toolchain,
    read_text_if_exists,
    requested_workspace_root,
    run_verification_readiness_check,
    shared_workspace_root,
    subprocess_env_for_tool,
    writability_error,
)
from process_runner import CommandSpec, SubprocessRunner, coerce_text
from script_output import PayloadEmitter, append_unique as append_unique_message


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
        "--target",
        choices=["search", "verify"],
        default="search",
        help="Desired readiness level. `search` prepares theorem search; `verify` additionally prepares Lean verification artifacts.",
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
        "--build-mathlib",
        action="store_true",
        help="Allow `lake build Mathlib` as a heavyweight fallback when `Mathlib.olean` is still missing.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip the final `lean_check.py` smoke test when `--target verify` is used.",
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
    return coerce_text(value)


def append_unique(items: list[str], message: str | None) -> None:
    append_unique_message(items, message)


def emit_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    PayloadEmitter(json_enabled=args.json, human_printer=print_human).emit(payload)


def run_command(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> dict[str, object]:
    return SubprocessRunner().run(
        CommandSpec(
            command=command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            include_duration=True,
        )
    )


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


def sync_project_toolchain(proofs_dir: Path) -> tuple[str | None, str | None]:
    toolchain_path = proofs_dir / "lean-toolchain"
    current = read_text_if_exists(toolchain_path)
    normalized = normalize_lean_toolchain(current)
    if current is None or normalized is None:
        return current, current

    prefix = "leanprover/lean4"
    if ":" in current:
        raw_prefix, _ = current.split(":", 1)
        prefix = raw_prefix.strip() or prefix
    canonical = f"{prefix}:{normalized}"
    if current != canonical:
        toolchain_path.write_text(f"{canonical}\n", encoding="utf-8")
    return current, canonical


def expected_mathlib_revision(proofs_dir: Path) -> str | None:
    return mathlib_revision_for_toolchain(read_text_if_exists(proofs_dir / "lean-toolchain"))


def sync_mathlib_dependency_revision(proofs_dir: Path) -> tuple[str | None, str | None]:
    expected_rev = expected_mathlib_revision(proofs_dir)
    if expected_rev is None:
        return None, None

    lakefile = proofs_dir / "lakefile.toml"
    contents = read_text_if_exists(lakefile)
    if contents is None:
        return expected_rev, None

    pattern = re.compile(
        r'(\[\[require\]\]\s*name = "mathlib"\s*scope = "leanprover-community"\s*rev = ")([^"]+)(")',
        re.MULTILINE,
    )
    match = pattern.search(contents)
    if match is None:
        return expected_rev, None

    current_rev = match.group(2)
    if current_rev == expected_rev:
        return expected_rev, current_rev

    updated = pattern.sub(rf'\1{expected_rev}\3', contents, count=1)
    lakefile.write_text(updated, encoding="utf-8")
    return expected_rev, current_rev


def incompatible_mathlib_checkout_reason(proofs_dir: Path) -> str | None:
    mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib"
    if not mathlib_dir.exists():
        return None

    mathlib_source = mathlib_dir / "Mathlib"
    if not mathlib_source.exists():
        return "the cached mathlib checkout is incomplete"

    project_toolchain = normalize_lean_toolchain(read_text_if_exists(proofs_dir / "lean-toolchain"))
    mathlib_toolchain = normalize_lean_toolchain(read_text_if_exists(mathlib_dir / "lean-toolchain"))
    if project_toolchain and mathlib_toolchain and project_toolchain != mathlib_toolchain:
        return (
            f"mathlib requires Lean {mathlib_toolchain} but the shared proofs project is pinned to Lean {project_toolchain}"
        )

    return None


def remove_tree(path: Path) -> None:
    def onerror(func: object, target: str, exc_info: tuple[object, object, object]) -> None:
        if not os.path.exists(target):
            return
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)

    shutil.rmtree(path, onerror=onerror)


def repair_mathlib_checkout(proofs_dir: Path) -> str | None:
    reason = incompatible_mathlib_checkout_reason(proofs_dir)
    if reason is None:
        return None

    reset_mathlib_checkout(proofs_dir)
    return reason


def reset_mathlib_checkout(proofs_dir: Path) -> None:
    mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib"
    manifest = proofs_dir / "lake-manifest.json"
    if mathlib_dir.exists():
        remove_tree(mathlib_dir)
    if manifest.exists():
        manifest.unlink()


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
    print(f"target readiness: {payload['target']}")
    environment = payload.get("environment", {})
    if environment:
        print(f"codex home: {environment.get('codex_home')}")
        print(f"tool HOME: {environment.get('home') or 'unset'}")
        print(f"tool ELAN_HOME: {environment.get('elan_home') or 'unset'}")
    print(f"project initialized: {payload['project_initialized']}")
    print(f"scratch file present: {payload['proof_scratch_exists']}")
    print(f"readiness level: {payload.get('readiness_level', 'incomplete')}")
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
    wants_verification = args.target == "verify"
    should_build_mathlib = args.build_mathlib or wants_verification
    should_run_verify = wants_verification and not args.skip_verify

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
        "target": args.target,
        "build_mathlib_requested": should_build_mathlib,
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
        "readiness_level": "incomplete",
    }

    if ignored_local_workspace is not None:
        append_unique(
            payload["warnings"],
            f"Repo-local proofs at {ignored_local_workspace} are ignored in shared-workspace mode.",
        )

    if lake is None:
        append_unique(
            payload["next_steps"],
            "Run `python scripts/setup_plugin.py --target search --yes` to populate the shared environment, then retry the advanced bootstrap command only if you still need manual recovery.",
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

    try:
        previous_toolchain, canonical_toolchain = sync_project_toolchain(proofs_dir)
    except OSError as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        append_unique(
            payload["next_steps"],
            "Could not update `proofs/lean-toolchain`. Ensure the shared proofs workspace is writable and rerun bootstrap.",
        )
        emit_payload(args, payload)
        return 6
    toolchain_changed = previous_toolchain is not None and canonical_toolchain is not None and previous_toolchain != canonical_toolchain
    if toolchain_changed:
        append_unique(
            payload["warnings"],
            f"Normalized the shared Lean toolchain from `{previous_toolchain}` to `{canonical_toolchain}`.",
        )

    try:
        expected_rev, previous_rev = sync_mathlib_dependency_revision(proofs_dir)
    except OSError as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        append_unique(
            payload["next_steps"],
            "Could not update `proofs/lakefile.toml`. Ensure the shared proofs workspace is writable and rerun bootstrap.",
        )
        emit_payload(args, payload)
        return 6
    payload["expected_mathlib_revision"] = expected_rev
    dependency_changed = expected_rev is not None and previous_rev is not None and previous_rev != expected_rev
    if dependency_changed:
        append_unique(
            payload["warnings"],
            f"Pinned mathlib from `{previous_rev}` to `{expected_rev}` to match the shared Lean toolchain.",
        )

    try:
        repaired_mathlib_reason = repair_mathlib_checkout(proofs_dir)
    except OSError as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        append_unique(
            payload["next_steps"],
            "Could not clean the incompatible cached mathlib checkout under `proofs/.lake/packages/mathlib`. Ensure the shared proofs workspace is writable and rerun bootstrap.",
        )
        emit_payload(args, payload)
        return 6
    if repaired_mathlib_reason is not None:
        append_unique(
            payload["warnings"],
            f"Removed the cached mathlib checkout because {repaired_mathlib_reason}.",
        )

    mathlib_source = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
    manifest_path = proofs_dir / "lake-manifest.json"
    needs_update = (
        repaired_mathlib_reason is not None
        or toolchain_changed
        or dependency_changed
        or not mathlib_source.exists()
        or not manifest_path.exists()
    )
    can_attempt_cache = True
    if not args.skip_update and needs_update:
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
        if not update_step["success"] and update_step["reason"] == "package_state":
            try:
                reset_mathlib_checkout(proofs_dir)
            except OSError as exc:
                payload["error"] = f"{type(exc).__name__}: {exc}"
                append_unique(
                    payload["next_steps"],
                    "Could not reset the cached mathlib checkout after `lake update` reported a package-state error.",
                )
                emit_payload(args, payload)
                return 6

            append_unique(
                payload["warnings"],
                "`lake update` reported a package-state error; removed the cached mathlib checkout and retried from a fresh clone.",
            )
            retry_step = run_command(
                [str(lake), "update"],
                cwd=proofs_dir,
                env=env,
                timeout_seconds=args.timeout_seconds,
            )
            retry_step["name"] = "lake update (retry)"
            retry_step["reason"] = classify_step_failure(retry_step)
            retry_step["guidance"] = failure_guidance("lake update", retry_step["reason"], selected_scope)
            payload["steps"].append(retry_step)
            update_step = retry_step

        if not update_step["success"]:
            if mathlib_source.exists() and manifest_path.exists():
                update_step["non_fatal"] = True
                payload["warnings"].append(
                    f"`lake update` reported {update_step['reason']} but mathlib sources and the package manifest are present, so bootstrap continued."
                )
            else:
                can_attempt_cache = False
                append_unique(payload["next_steps"], update_step["guidance"])

    mathlib_artifact = mathlib_module_artifact(proofs_dir)
    cache_needed = len(discover_package_lib_dirs(proofs_dir)) == 0 or not mathlib_artifact.exists()
    if can_attempt_cache and not args.skip_cache and cache_needed:
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

    mathlib_artifact = mathlib_module_artifact(proofs_dir)
    build_required = can_attempt_cache and should_build_mathlib and not mathlib_artifact.exists()
    if can_attempt_cache and should_build_mathlib and mathlib_artifact.exists():
        readiness_smoke = run_verification_readiness_check(
            proofs_dir,
            timeout_seconds=args.timeout_seconds,
        )
        if not bool(readiness_smoke.get("success")):
            build_required = True
            append_unique(
                payload["warnings"],
                "The shared Mathlib import smoke check failed even though `Mathlib.olean` exists, so bootstrap will force `lake build Mathlib`.",
            )

    if build_required:
        build_step = run_command(
            [str(lake), "build", "Mathlib"],
            cwd=proofs_dir,
            env=env,
            timeout_seconds=args.timeout_seconds,
        )
        build_step["name"] = "lake build Mathlib"
        build_step["reason"] = classify_step_failure(build_step)
        build_step["guidance"] = failure_guidance("lake build Mathlib", build_step["reason"], selected_scope)
        payload["steps"].append(build_step)
        if not build_step["success"]:
            if mathlib_artifact.exists():
                build_step["non_fatal"] = True
                payload["warnings"].append(
                    f"`lake build Mathlib` reported {build_step['reason']} but `Mathlib.olean` is present, so bootstrap continued."
                )
            else:
                append_unique(payload["next_steps"], build_step["guidance"])

    if not should_build_mathlib and not mathlib_artifact.exists():
        append_unique(
            payload["warnings"],
            "Search-ready bootstrap stops before `lake build Mathlib`; verification artifacts are still missing.",
        )
        append_unique(
            payload["next_steps"],
            "Run `python scripts/setup_plugin.py --target verify --yes` when you need full Lean verification artifacts.",
        )

    if should_run_verify:
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
    search_ready = (
        payload["project_initialized"]
        and payload["proof_scratch_exists"]
        and mathlib_source.exists()
    )
    verification_ready = (
        search_ready
        and mathlib_artifact.exists()
        and len(package_lib_dirs) > 0
    )
    verification_success = bool(payload["verification"].get("success")) if payload["verification"] else None
    if should_run_verify and verification_success is False:
        verification_ready = False
    if verification_ready:
        readiness_level = "verification-ready"
    elif search_ready:
        readiness_level = "search-ready"
    else:
        readiness_level = "incomplete"
    payload["postconditions"] = {
        "mathlib_source_exists": mathlib_source.exists(),
        "mathlib_artifact_exists": mathlib_artifact.exists(),
        "package_library_path_count": len(package_lib_dirs),
        "ready_for_search": search_ready,
        "ready_for_verification": verification_ready,
        "readiness_level": readiness_level,
        "verification_success": verification_success,
    }
    payload["readiness_level"] = readiness_level

    if not args.skip_update and not payload["postconditions"]["mathlib_source_exists"]:
        append_unique(
            payload["next_steps"],
            "Mathlib sources are still missing under `proofs/.lake/packages/mathlib`. Run `lake update` again once the environment issue is fixed.",
        )
    if not args.skip_cache and payload["postconditions"]["package_library_path_count"] == 0:
        append_unique(
            payload["next_steps"],
            "Compiled package libraries are still missing. Search can continue, but Lean verification will still require `lake exe cache get` or explicit verify setup.",
        )
    if not payload["postconditions"]["mathlib_artifact_exists"]:
        if should_build_mathlib:
            append_unique(
                payload["next_steps"],
                "`Mathlib.olean` is still missing under `proofs/.lake/packages/mathlib/.lake/build/lib/lean`. Run `lake build Mathlib` again or rerun verify setup with a larger timeout.",
            )

    target_ready = payload["postconditions"]["ready_for_verification"] if wants_verification else payload["postconditions"]["ready_for_search"]
    payload["success"] = bool(target_ready) and (not should_run_verify or bool(payload["postconditions"]["verification_success"]))

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

