from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from script_output import append_unique


@dataclass(frozen=True)
class ToolchainBootstrapDependencies:
    resolve_fallback_tool_homes: Callable[[], tuple[Path, Path]]
    writability_error: Callable[[Path | str | None], str | None]
    prepare_writable_directory: Callable[[Path], bool]
    refresh_cached_tools: Callable[[dict[str, object]], tuple[Path | None, Path | None, Path | None]]
    find_elan: Callable[[], Path | None]
    cache_elan_binary: Callable[[Path, Path], Path]
    active_toolchain_root: Callable[[Path, int], tuple[Path | None, dict[str, object]]]
    copy_toolchain_tree: Callable[[Path, Path, bool], tuple[Path, dict[str, object]]]
    safe_resolve: Callable[[Path], Path]
    build_tool_env: Callable[[Path, Path, Path], dict[str, str]]
    run_command: Callable[[list[str], Path, dict[str, str], int], dict[str, object]]
    plugin_root: Callable[[], Path]


class ToolchainBootstrapService:
    def __init__(self, dependencies: ToolchainBootstrapDependencies) -> None:
        self._deps = dependencies

    def bootstrap(self, args: object) -> tuple[dict[str, object], int]:
        target_home, target_elan_home = self._deps.resolve_fallback_tool_homes()
        payload: dict[str, object] = {
            "status": "failure",
            "target_home": str(target_home),
            "target_elan_home": str(target_elan_home),
            "source_elan_path": None,
            "cached_elan_path": None,
            "cached_lake_path": None,
            "cached_lean_path": None,
            "cached_lake_version": None,
            "cached_lean_version": None,
            "steps": [],
            "warnings": [],
            "next_steps": [],
            "success": False,
        }

        home_error = self._deps.writability_error(target_home)
        elan_error = self._deps.writability_error(target_elan_home)
        if home_error is not None and not self._deps.prepare_writable_directory(target_home):
            append_unique(payload["next_steps"], f"Tool HOME is not writable: {target_home} ({home_error}).")
        if elan_error is not None and not self._deps.prepare_writable_directory(target_elan_home):
            append_unique(payload["next_steps"], f"ELAN_HOME is not writable: {target_elan_home} ({elan_error}).")
        if payload["next_steps"]:
            append_unique(
                payload["next_steps"],
                "Set CODEX_HOME to a writable directory and rerun bootstrap_toolchain.py.",
            )
            return payload, 3

        _, cached_lake, cached_lean = self._deps.refresh_cached_tools(payload)
        if cached_lake is not None and cached_lean is not None and not bool(getattr(args, "force")):
            payload["success"] = True
            payload["status"] = "success"
            return payload, 0

        elan = self._deps.find_elan()
        payload["source_elan_path"] = str(elan) if elan else None
        if elan is None:
            append_unique(
                payload["next_steps"],
                "No `elan` executable was found. Install Lean once globally or provide a portable `elan` binary, then rerun bootstrap_toolchain.py.",
            )
            return payload, 2

        cached_elan = self._deps.cache_elan_binary(elan, target_elan_home)
        payload["steps"].append(
            {
                "name": "cache elan binary",
                "success": True,
                "stdout": f"Cached elan at {cached_elan}.",
                "stderr": "",
                "returncode": 0,
            }
        )

        active_root, host_step = self._deps.active_toolchain_root(elan, int(getattr(args, "timeout_seconds")))
        payload["steps"].append(host_step)
        if active_root is not None:
            copied_root, copy_step = self._deps.copy_toolchain_tree(active_root, target_elan_home, bool(getattr(args, "force")))
            payload["steps"].append(copy_step)
            if self._deps.safe_resolve(copied_root) == self._deps.safe_resolve(active_root):
                append_unique(
                    payload["warnings"],
                    "The active Lean toolchain was already under the plugin cache; no host-to-cache copy was needed.",
                )

        _, cached_lake, cached_lean = self._deps.refresh_cached_tools(payload)
        if (cached_lake is None or cached_lean is None) and not bool(getattr(args, "skip_install")):
            target_elan = cached_elan if cached_elan.exists() else elan
            tool_env = self._deps.build_tool_env(target_elan, target_home, target_elan_home)
            install_step = self._deps.run_command(
                [str(target_elan), "toolchain", "install", str(getattr(args, "toolchain"))],
                cwd=self._deps.plugin_root(),
                env=tool_env,
                timeout_seconds=int(getattr(args, "timeout_seconds")),
            )
            install_step["name"] = "elan toolchain install"
            payload["steps"].append(install_step)

            if install_step["success"]:
                default_step = self._deps.run_command(
                    [str(target_elan), "default", str(getattr(args, "toolchain"))],
                    cwd=self._deps.plugin_root(),
                    env=tool_env,
                    timeout_seconds=int(getattr(args, "timeout_seconds")),
                )
                default_step["name"] = "elan default"
                payload["steps"].append(default_step)
                if not default_step["success"]:
                    append_unique(
                        payload["warnings"],
                        "`elan default` did not complete cleanly. The plugin will still use direct toolchain binaries when they are present in the cache.",
                    )

        _, cached_lake, cached_lean = self._deps.refresh_cached_tools(payload)
        payload["success"] = cached_lake is not None and cached_lean is not None
        payload["status"] = "success" if payload["success"] else "failure"

        if not payload["success"]:
            if bool(getattr(args, "skip_install")):
                append_unique(
                    payload["next_steps"],
                    "No cached `lake` / `lean` binaries were produced from the active host toolchain. Rerun without `--skip-install` to let `elan` install into the plugin cache.",
                )
            else:
                append_unique(
                    payload["next_steps"],
                    "The plugin-local toolchain is still incomplete. Check the `elan` stderr above and rerun once network access or toolchain installation is available.",
                )

        return payload, 0 if payload["success"] else 5
