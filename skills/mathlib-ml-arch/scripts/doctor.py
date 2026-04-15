from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

from common import (
    cached_elan_homes,
    codex_home,
    configure_stdout,
    discover_package_lib_dirs,
    find_elan,
    find_existing_proofs_root,
    find_shared_proofs_root,
    find_lake,
    find_lean,
    path_contains,
    requested_workspace_root,
    resolve_proofs_workspace,
    shared_workspace_root,
    subprocess_env_for_tool,
    writability_error,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect the shared Lean/mathlib workspace and emit agent-friendly diagnostics."
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root or child directory to inspect. Defaults to the current directory.",
    )
    parser.add_argument(
        "--scope",
        choices=["auto", "local", "shared"],
        default="auto",
        help="Which proofs workspace to inspect. The plugin is shared-workspace-only; `auto`, `shared`, and legacy `local` all resolve to the shared CODEX_HOME cache.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )
    return parser.parse_args()


def detect_tool_version(tool: Path | None) -> str | None:
    if tool is None:
        return None

    try:
        result = subprocess.run(
            [str(tool), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            env=subprocess_env_for_tool(tool),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    output = (result.stdout or result.stderr).strip()
    if not output:
        return None
    return output.splitlines()[0]


def build_payload(requested_workspace: Path, scope: str) -> dict[str, object]:
    local_workspace = find_existing_proofs_root(requested_workspace)
    shared_workspace = find_shared_proofs_root()
    shared_config_root = shared_workspace_root()
    workspace_root, selected_scope = resolve_proofs_workspace(requested_workspace, scope)
    ignored_local_workspace = (
        local_workspace
        if local_workspace is not None and not path_contains(local_workspace, shared_config_root)
        else None
    )
    inspected_root = workspace_root or shared_config_root
    proofs_dir = inspected_root / "proofs"
    lake = find_lake()
    lean = find_lean(lake)
    elan = find_elan()
    lib_dirs = discover_package_lib_dirs(proofs_dir) if proofs_dir.exists() else []
    mathlib_source = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
    proof_scratch = proofs_dir / "ProofScratch.lean"
    tool_env = subprocess_env_for_tool(lake or lean)
    lean_source_count = 0
    if proofs_dir.exists():
        lean_source_count = sum(1 for _ in proofs_dir.rglob("*.lean"))

    shared_workspace_write_error = writability_error(shared_config_root)
    selected_workspace_write_error = writability_error(inspected_root)

    payload = {
        "platform": {
            "os_name": sys.platform,
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
        },
        "requested_workspace": str(requested_workspace),
        "workspace_root": str(inspected_root),
        "selected_workspace_root": str(workspace_root) if workspace_root else None,
        "selected_scope": selected_scope,
        "requested_scope": scope,
        "local_workspace_root": str(local_workspace) if local_workspace else None,
        "ignored_local_workspace_root": str(ignored_local_workspace) if ignored_local_workspace else None,
        "shared_workspace_root": str(shared_workspace) if shared_workspace else None,
        "shared_workspace_config_root": str(shared_config_root),
        "shared_workspace_writable": shared_workspace_write_error is None,
        "shared_workspace_write_error": shared_workspace_write_error,
        "selected_workspace_writable": selected_workspace_write_error is None,
        "selected_workspace_write_error": selected_workspace_write_error,
        "proofs_dir": str(proofs_dir),
        "proofs_exists": proofs_dir.is_dir(),
        "lean_toolchain_exists": (proofs_dir / "lean-toolchain").exists(),
        "lakefile_exists": (proofs_dir / "lakefile.toml").exists(),
        "manifest_exists": (proofs_dir / "lake-manifest.json").exists(),
        "proof_scratch_exists": proof_scratch.exists(),
        "mathlib_source_exists": mathlib_source.exists(),
        "lean_source_count": lean_source_count,
        "package_library_paths": [str(path) for path in lib_dirs],
        "package_library_path_count": len(lib_dirs),
        "lake_path": str(lake) if lake else None,
        "lake_version": detect_tool_version(lake),
        "lake_from_plugin_cache": any(path_contains(lake, elan_home) for elan_home in cached_elan_homes()) if lake else False,
        "lean_path": str(lean) if lean else None,
        "lean_version": detect_tool_version(lean),
        "lean_from_plugin_cache": any(path_contains(lean, elan_home) for elan_home in cached_elan_homes()) if lean else False,
        "elan_path": str(elan) if elan else None,
        "elan_version": detect_tool_version(elan),
        "elan_from_plugin_cache": any(path_contains(elan, elan_home) for elan_home in cached_elan_homes()) if elan else False,
        "codex_home": str(codex_home()),
        "tool_home": tool_env.get("HOME"),
        "tool_elan_home": tool_env.get("ELAN_HOME"),
        "ready_for_search": proofs_dir.is_dir() and lean_source_count > 0,
        "ready_for_lake_check": proofs_dir.is_dir() and lake is not None and proof_scratch.exists(),
        "ready_for_direct_lean": proofs_dir.is_dir() and lean is not None and proof_scratch.exists() and len(lib_dirs) > 0,
        "next_steps": [],
    }

    next_steps: list[str] = []
    if ignored_local_workspace is not None:
        next_steps.append(
            f"Repo-local proofs at {ignored_local_workspace} are ignored in shared-workspace mode. Remove or archive them if they are stale."
        )
    if not payload["proofs_exists"]:
        next_steps.append(
            "Run bootstrap_proofs.py to create or refresh the shared proofs project under CODEX_HOME."
        )
    if not payload["shared_workspace_writable"]:
        next_steps.append(
            "The shared CODEX_HOME cache is not writable. Set CODEX_HOME to a writable directory."
        )
    if payload["proofs_exists"] and not payload["lean_toolchain_exists"]:
        next_steps.append("Create proofs/lean-toolchain or rerun bootstrap_proofs.py.")
    if payload["proofs_exists"] and not payload["lakefile_exists"]:
        next_steps.append("Create proofs/lakefile.toml or rerun bootstrap_proofs.py.")
    if lake is None:
        next_steps.append(
            "Run bootstrap_toolchain.py to populate the plugin-local Lean toolchain cache, or install Lean 4 globally so `lake` is available."
        )
    if lean is None:
        next_steps.append(
            "Run bootstrap_toolchain.py to populate the plugin-local Lean toolchain cache, or install Lean 4 globally so `lean` is available."
        )
    if payload["proofs_exists"] and not payload["mathlib_source_exists"]:
        next_steps.append("Run `lake update` inside proofs/ to fetch mathlib sources.")
    if payload["proofs_exists"] and len(lib_dirs) == 0:
        next_steps.append("Run `lake exe cache get` or build the proofs project to populate `.olean` files.")
    if payload["proofs_exists"] and not payload["proof_scratch_exists"]:
        next_steps.append("Create proofs/ProofScratch.lean for theorem verification.")

    payload["next_steps"] = next_steps
    return payload


def print_human(payload: dict[str, object]) -> None:
    print(f"requested workspace: {payload['requested_workspace']}")
    selected_root = payload["selected_workspace_root"] or "none"
    selected_scope = payload["selected_scope"] or "none"
    print(f"selected proofs workspace: {selected_root} ({selected_scope})")
    print(f"shared proofs workspace: {payload['shared_workspace_root'] or 'missing'}")
    if payload.get("ignored_local_workspace_root"):
        print(f"ignored repo-local proofs workspace: {payload['ignored_local_workspace_root']}")
    print(f"shared cache root: {payload['shared_workspace_config_root']}")
    print(f"shared cache writable: {payload['shared_workspace_writable']}")
    print(f"selected workspace writable: {payload['selected_workspace_writable']}")
    print(f"proofs/: {'present' if payload['proofs_exists'] else 'missing'}")
    print(f"lake: {payload['lake_path'] or 'not found'}")
    if payload.get("lake_version"):
        print(f"  version: {payload['lake_version']}")
    print(f"  plugin cache: {payload['lake_from_plugin_cache']}")
    print(f"lean: {payload['lean_path'] or 'not found'}")
    if payload.get("lean_version"):
        print(f"  version: {payload['lean_version']}")
    print(f"  plugin cache: {payload['lean_from_plugin_cache']}")
    print(f"elan: {payload.get('elan_path') or 'not found'}")
    if payload.get("elan_version"):
        print(f"  version: {payload['elan_version']}")
    print(f"  plugin cache: {payload['elan_from_plugin_cache']}")
    print(f"codex home: {payload['codex_home']}")
    print(f"tool HOME: {payload.get('tool_home') or 'unset'}")
    print(f"tool ELAN_HOME: {payload.get('tool_elan_home') or 'unset'}")
    print(f"mathlib sources: {'present' if payload['mathlib_source_exists'] else 'missing'}")
    print(f"lean source files: {payload['lean_source_count']}")
    print(f"compiled library paths: {payload['package_library_path_count']}")
    print(f"ready for search: {payload['ready_for_search']}")
    print(f"ready for lake check: {payload['ready_for_lake_check']}")
    print(f"ready for direct lean: {payload['ready_for_direct_lean']}")
    if payload["next_steps"]:
        print("next steps:")
        for step in payload["next_steps"]:
            print(f"  - {step}")


def main() -> int:
    configure_stdout()
    args = parse_args()
    workspace_root = requested_workspace_root(args.workspace)
    payload = build_payload(workspace_root, args.scope)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(payload)

    return 0 if payload["ready_for_search"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
