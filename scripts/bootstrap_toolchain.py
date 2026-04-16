from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from common import (
    configure_stdout,
    find_cached_tool,
    find_elan,
    prepare_writable_directory,
    resolve_fallback_tool_homes,
    safe_resolve,
    subprocess_env_for_tool,
    writability_error,
)
from process_runner import CommandSpec, SubprocessRunner, detect_tool_version as probe_tool_version
from script_output import PayloadEmitter, append_unique as append_unique_message
from toolchain_bootstrap_service import ToolchainBootstrapDependencies, ToolchainBootstrapService


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate a plugin-local Lean/Lake toolchain cache under CODEX_HOME instead of relying on the host profile."
    )
    parser.add_argument(
        "--toolchain",
        default="stable",
        help="Lean toolchain passed to `elan toolchain install` when a cached copy cannot be produced from the host toolchain.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-copy the active host toolchain into the plugin cache even when a cached toolchain already exists.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Do not call `elan toolchain install`; only copy an already-active host toolchain into the plugin cache.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Per-command timeout for `elan` subprocesses.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable diagnostics.",
    )
    return parser.parse_args()


def detect_tool_version(tool: Path | None) -> str | None:
    return probe_tool_version(tool, subprocess_env_for_tool)


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
    runner = SubprocessRunner()
    return runner.run(CommandSpec(command=command, cwd=cwd, env=env, timeout_seconds=timeout_seconds))


def build_tool_env(elan: Path, target_home: Path, target_elan_home: Path) -> dict[str, str]:
    env = subprocess_env_for_tool(elan)
    env["HOME"] = str(target_home)
    env["USERPROFILE"] = str(target_home)
    env["ELAN_HOME"] = str(target_elan_home)
    env["PATH"] = f"{target_elan_home / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    return env


def cache_elan_binary(source_elan: Path, target_elan_home: Path) -> Path:
    target_bin = target_elan_home / "bin"
    target_bin.mkdir(parents=True, exist_ok=True)
    target = safe_resolve(target_bin / source_elan.name)
    if safe_resolve(source_elan) != target:
        shutil.copy2(source_elan, target)
    return target


def active_toolchain_root(elan: Path, timeout_seconds: int) -> tuple[Path | None, dict[str, object]]:
    step = run_command(
        [str(elan), "which", "lean"],
        cwd=plugin_root(),
        env=subprocess_env_for_tool(elan),
        timeout_seconds=timeout_seconds,
    )
    step["name"] = "elan which lean"
    if not step["success"]:
        return None, step

    line = next((part.strip() for part in str(step.get("stdout", "")).splitlines() if part.strip()), "")
    candidate = Path(line).expanduser() if line else None
    if candidate is None or not candidate.exists() or candidate.parent.name != "bin":
        step["success"] = False
        step["stderr"] = f"{step.get('stderr', '')}\nCould not resolve the active Lean toolchain root.".strip()
        return None, step

    return safe_resolve(candidate.parent.parent), step


def copy_toolchain_tree(source_root: Path, target_elan_home: Path, force: bool) -> tuple[Path, dict[str, object]]:
    target_root = safe_resolve(target_elan_home / "toolchains" / source_root.name)
    target_root.parent.mkdir(parents=True, exist_ok=True)

    if target_root.exists() and not force:
        return target_root, {
            "name": "copy active toolchain",
            "success": True,
            "stdout": f"Toolchain already cached at {target_root}.",
            "stderr": "",
            "returncode": 0,
        }

    shutil.copytree(source_root, target_root, dirs_exist_ok=True)
    return target_root, {
        "name": "copy active toolchain",
        "success": True,
        "stdout": f"Copied {source_root} to {target_root}.",
        "stderr": "",
        "returncode": 0,
    }


def refresh_cached_tools(payload: dict[str, object]) -> tuple[Path | None, Path | None, Path | None]:
    cached_elan = find_cached_tool("elan")
    cached_lake = find_cached_tool("lake")
    cached_lean = find_cached_tool("lean")
    payload["cached_elan_path"] = str(cached_elan) if cached_elan else None
    payload["cached_lake_path"] = str(cached_lake) if cached_lake else None
    payload["cached_lean_path"] = str(cached_lean) if cached_lean else None
    payload["cached_lake_version"] = detect_tool_version(cached_lake)
    payload["cached_lean_version"] = detect_tool_version(cached_lean)
    return cached_elan, cached_lake, cached_lean


def print_human(payload: dict[str, object]) -> None:
    print(f"status: {payload['status']}")
    print(f"target HOME: {payload['target_home']}")
    print(f"target ELAN_HOME: {payload['target_elan_home']}")
    print(f"source elan: {payload.get('source_elan_path') or 'missing'}")
    print(f"cached elan: {payload.get('cached_elan_path') or 'missing'}")
    print(f"cached lake: {payload.get('cached_lake_path') or 'missing'}")
    if payload.get("cached_lake_version"):
        print(f"  version: {payload['cached_lake_version']}")
    print(f"cached lean: {payload.get('cached_lean_path') or 'missing'}")
    if payload.get("cached_lean_version"):
        print(f"  version: {payload['cached_lean_version']}")
    for step in payload["steps"]:
        outcome = "ok" if step.get("success") else "failed"
        print(f"{step['name']}: {outcome}")
        stderr = str(step.get("stderr", "")).strip()
        if stderr:
            print(f"  stderr: {stderr.splitlines()[-1]}")
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
    payload, exit_code = ToolchainBootstrapService(
        ToolchainBootstrapDependencies(
            resolve_fallback_tool_homes=resolve_fallback_tool_homes,
            writability_error=writability_error,
            prepare_writable_directory=prepare_writable_directory,
            refresh_cached_tools=refresh_cached_tools,
            find_elan=find_elan,
            cache_elan_binary=cache_elan_binary,
            active_toolchain_root=active_toolchain_root,
            copy_toolchain_tree=copy_toolchain_tree,
            safe_resolve=safe_resolve,
            build_tool_env=build_tool_env,
            run_command=run_command,
            plugin_root=plugin_root,
        )
    ).bootstrap(args)
    emit_payload(args, payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

