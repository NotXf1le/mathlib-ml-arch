from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DoctorDependencies:
    find_existing_proofs_root: Callable[[Path], Path | None]
    find_shared_proofs_root: Callable[[], Path | None]
    shared_workspace_root: Callable[[], Path]
    resolve_proofs_workspace: Callable[[Path, str], tuple[Path | None, str | None]]
    proofs_workspace_status: Callable[..., dict[str, object]]
    discover_package_lib_dirs: Callable[[Path], list[Path]]
    find_lake: Callable[[], Path | None]
    find_lean: Callable[[Path | None], Path | None]
    find_elan: Callable[[], Path | None]
    cached_elan_homes: Callable[[], list[Path]]
    path_contains: Callable[[Path, Path], bool]
    writability_error: Callable[[Path | str | None], str | None]
    codex_home: Callable[[], Path]
    subprocess_env_for_tool: Callable[[Path | None], dict[str, str]]
    detect_tool_version: Callable[[Path | None], str | None]


class DoctorPayloadBuilder:
    def __init__(self, dependencies: DoctorDependencies) -> None:
        self._deps = dependencies

    def build(self, requested_workspace: Path, scope: str) -> dict[str, object]:
        local_workspace = self._deps.find_existing_proofs_root(requested_workspace)
        shared_workspace = self._deps.find_shared_proofs_root()
        shared_config_root = self._deps.shared_workspace_root()
        workspace_root, selected_scope = self._deps.resolve_proofs_workspace(requested_workspace, scope)
        ignored_local_workspace = (
            local_workspace
            if local_workspace is not None and not self._deps.path_contains(local_workspace, shared_config_root)
            else None
        )
        inspected_root = workspace_root or shared_config_root
        proofs_dir = inspected_root / "proofs"
        lake = self._deps.find_lake()
        lean = self._deps.find_lean(lake)
        elan = self._deps.find_elan()
        lib_dirs = self._deps.discover_package_lib_dirs(proofs_dir) if proofs_dir.exists() else []
        mathlib_source = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
        proof_scratch = proofs_dir / "ProofScratch.lean"
        tool_env = self._deps.subprocess_env_for_tool(lake or lean)
        lean_source_count = 0
        if proofs_dir.exists():
            lean_source_count = sum(1 for _ in proofs_dir.rglob("*.lean"))

        shared_workspace_write_error = self._deps.writability_error(shared_config_root)
        selected_workspace_write_error = self._deps.writability_error(inspected_root)
        workspace_status = self._deps.proofs_workspace_status(workspace_root, verify_with_tooling=True)
        verification_smoke = workspace_status.get("verification_smoke")

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
            "project_toolchain": workspace_status["project_toolchain"],
            "mathlib_toolchain": workspace_status["mathlib_toolchain"],
            "toolchain_compatible": bool(workspace_status["toolchain_compatible"]),
            "lean_toolchain_exists": (proofs_dir / "lean-toolchain").exists(),
            "lakefile_exists": (proofs_dir / "lakefile.toml").exists(),
            "manifest_exists": (proofs_dir / "lake-manifest.json").exists(),
            "proof_scratch_exists": proof_scratch.exists(),
            "mathlib_source_exists": mathlib_source.exists(),
            "mathlib_artifact_exists": bool(workspace_status["mathlib_artifact_exists"]),
            "lean_source_count": lean_source_count,
            "package_library_paths": [str(path) for path in lib_dirs],
            "package_library_path_count": len(lib_dirs),
            "lake_path": str(lake) if lake else None,
            "lake_version": self._deps.detect_tool_version(lake),
            "lake_from_plugin_cache": any(
                self._deps.path_contains(lake, elan_home) for elan_home in self._deps.cached_elan_homes()
            ) if lake else False,
            "lean_path": str(lean) if lean else None,
            "lean_version": self._deps.detect_tool_version(lean),
            "lean_from_plugin_cache": any(
                self._deps.path_contains(lean, elan_home) for elan_home in self._deps.cached_elan_homes()
            ) if lean else False,
            "elan_path": str(elan) if elan else None,
            "elan_version": self._deps.detect_tool_version(elan),
            "elan_from_plugin_cache": any(
                self._deps.path_contains(elan, elan_home) for elan_home in self._deps.cached_elan_homes()
            ) if elan else False,
            "codex_home": str(self._deps.codex_home()),
            "tool_home": tool_env.get("HOME"),
            "tool_elan_home": tool_env.get("ELAN_HOME"),
            "ready_for_search": bool(workspace_status["ready_for_search"]),
            "ready_for_verification": bool(workspace_status["ready_for_verification"]),
            "readiness_level": str(workspace_status["readiness_level"]),
            "verification_smoke_checked": verification_smoke is not None,
            "verification_smoke_success": verification_smoke.get("success") if isinstance(verification_smoke, dict) else None,
            "verification_smoke_method": verification_smoke.get("verification_method") if isinstance(verification_smoke, dict) else None,
            "verification_smoke_error": verification_smoke.get("error") if isinstance(verification_smoke, dict) else None,
            "ready_for_lake_check": bool(workspace_status["ready_for_verification"]) and lake is not None and proof_scratch.exists(),
            "ready_for_direct_lean": bool(workspace_status["ready_for_verification"]) and lean is not None,
            "next_steps": [],
        }

        payload["next_steps"] = self._next_steps(payload, ignored_local_workspace, len(lib_dirs))
        return payload

    def _next_steps(
        self,
        payload: dict[str, object],
        ignored_local_workspace: Path | None,
        library_count: int,
    ) -> list[str]:
        next_steps: list[str] = []
        if ignored_local_workspace is not None:
            next_steps.append(
                f"Repo-local proofs at {ignored_local_workspace} are ignored in shared-workspace mode. Remove or archive them if they are stale."
            )
        if not payload["proofs_exists"]:
            next_steps.append(
                "Run `python scripts/setup_plugin.py --target search` to create or refresh the shared proofs project under CODEX_HOME."
            )
        if not payload["shared_workspace_writable"]:
            next_steps.append(
                "The shared CODEX_HOME cache is not writable. Set CODEX_HOME to a writable directory."
            )
        if payload["proofs_exists"] and not payload["lean_toolchain_exists"]:
            next_steps.append("Run `python scripts/setup_plugin.py --target search` to recreate the shared proofs project metadata.")
        if payload["proofs_exists"] and not payload["lakefile_exists"]:
            next_steps.append("Run `python scripts/setup_plugin.py --target search` to recreate the shared proofs project metadata.")
        if payload["lake_path"] is None:
            next_steps.append(
                "Run `python scripts/setup_plugin.py --target search` so `lake` is available to the plugin."
            )
        if payload["lean_path"] is None:
            next_steps.append(
                "Run `python scripts/setup_plugin.py --target verify --yes` so `lean` is available to the plugin."
            )
        if payload["proofs_exists"] and not payload["mathlib_source_exists"]:
            next_steps.append(
                "Run `python scripts/setup_plugin.py --target search` to populate the shared proofs workspace, fetch mathlib sources, and refresh the package cache."
            )
        if payload["proofs_exists"] and payload["mathlib_source_exists"] and not payload["toolchain_compatible"]:
            next_steps.append(
                "Run `python scripts/setup_plugin.py --target search` again so it can repair the shared mathlib checkout and repin it to the shared Lean toolchain."
            )
        if payload["proofs_exists"] and library_count == 0:
            next_steps.append(
                "Run `python scripts/setup_plugin.py --target verify` when you want compiled package libraries for Lean verification."
            )
        if payload["proofs_exists"] and payload["mathlib_source_exists"] and not payload["mathlib_artifact_exists"]:
            next_steps.append(
                "Run `python scripts/setup_plugin.py --target verify` to fetch or build `Mathlib.olean` for the shared proofs workspace."
            )
        if payload["verification_smoke_checked"] and payload["verification_smoke_success"] is False:
            next_steps.append(
                "Run `python scripts/setup_plugin.py --target verify --yes` again. If the shared environment still fails the import smoke test, inspect `python scripts/lean_check.py --json` because the cached Mathlib build is incomplete."
            )
        if payload["proofs_exists"] and not payload["proof_scratch_exists"]:
            next_steps.append("Run `python scripts/setup_plugin.py --target search` to recreate `proofs/ProofScratch.lean` in the shared workspace.")
        return next_steps
