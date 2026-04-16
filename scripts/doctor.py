from __future__ import annotations

import argparse
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
    proofs_workspace_status,
    shared_workspace_root,
    subprocess_env_for_tool,
    writability_error,
)
from doctor_service import DoctorDependencies, DoctorPayloadBuilder
from process_runner import detect_tool_version as probe_tool_version
from script_output import PayloadEmitter


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
    return probe_tool_version(tool, subprocess_env_for_tool)


def _payload_builder() -> DoctorPayloadBuilder:
    return DoctorPayloadBuilder(
        DoctorDependencies(
            find_existing_proofs_root=find_existing_proofs_root,
            find_shared_proofs_root=find_shared_proofs_root,
            shared_workspace_root=shared_workspace_root,
            resolve_proofs_workspace=resolve_proofs_workspace,
            proofs_workspace_status=proofs_workspace_status,
            discover_package_lib_dirs=discover_package_lib_dirs,
            find_lake=find_lake,
            find_lean=find_lean,
            find_elan=find_elan,
            cached_elan_homes=cached_elan_homes,
            path_contains=path_contains,
            writability_error=writability_error,
            codex_home=codex_home,
            subprocess_env_for_tool=subprocess_env_for_tool,
            detect_tool_version=detect_tool_version,
        )
    )


def build_payload(requested_workspace: Path, scope: str) -> dict[str, object]:
    return _payload_builder().build(requested_workspace, scope)


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
    print(f"project toolchain: {payload.get('project_toolchain') or 'missing'}")
    print(f"mathlib toolchain: {payload.get('mathlib_toolchain') or 'missing'}")
    print(f"toolchains compatible: {payload['toolchain_compatible']}")
    print(f"mathlib sources: {'present' if payload['mathlib_source_exists'] else 'missing'}")
    print(f"Mathlib.olean: {'present' if payload['mathlib_artifact_exists'] else 'missing'}")
    print(f"lean source files: {payload['lean_source_count']}")
    print(f"compiled library paths: {payload['package_library_path_count']}")
    print(f"readiness level: {payload['readiness_level']}")
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

    PayloadEmitter(json_enabled=args.json, human_printer=print_human).emit(payload)

    return 0 if payload["ready_for_search"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

