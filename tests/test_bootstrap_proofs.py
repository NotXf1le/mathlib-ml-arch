from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


sys.dont_write_bytecode = True

SKILL_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "mathlib-ml-arch" / "scripts"
if str(SKILL_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT_DIR))

import bootstrap_proofs  # noqa: E402


class BootstrapProofsTests(unittest.TestCase):
    def test_remove_tree_handles_read_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "readonly-tree"
            child = root / "nested"
            child.mkdir(parents=True)
            target = child / "file.txt"
            target.write_text("x", encoding="utf-8")
            os.chmod(target, stat.S_IREAD)

            bootstrap_proofs.remove_tree(root)

            self.assertFalse(root.exists())

    def test_expected_mathlib_revision_matches_project_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proofs_dir = Path(tmp)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:4.29.0", encoding="utf-8")

            self.assertEqual(bootstrap_proofs.expected_mathlib_revision(proofs_dir), "v4.29.0")

    def test_sync_project_toolchain_normalizes_to_canonical_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proofs_dir = Path(tmp)
            toolchain_path = proofs_dir / "lean-toolchain"
            toolchain_path.write_text("leanprover/lean4:4.29.0", encoding="utf-8")

            previous, canonical = bootstrap_proofs.sync_project_toolchain(proofs_dir)

            self.assertEqual(previous, "leanprover/lean4:4.29.0")
            self.assertEqual(canonical, "leanprover/lean4:v4.29.0")
            self.assertEqual(toolchain_path.read_text(encoding="utf-8").strip(), "leanprover/lean4:v4.29.0")

    def test_local_scope_aliases_to_shared_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            requested = Path(tmp)
            shared = requested / "shared_workspace"

            with patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared):
                root, scope, warnings = bootstrap_proofs.select_bootstrap_workspace(requested, "local")

            self.assertEqual(root, shared)
            self.assertEqual(scope, "shared")
            self.assertTrue(warnings)
            self.assertIn("no longer supported", warnings[0])

    def test_classifies_non_writable_elan_home(self) -> None:
        step = {
            "success": False,
            "stdout": "",
            "stderr": "error: could not create home directory C:\\Users\\CodexSandboxOffline\\.elan",
            "timed_out": False,
        }

        self.assertEqual(bootstrap_proofs.classify_step_failure(step), "home_directory")

    def test_classifies_reservoir_lookup_failure_as_network(self) -> None:
        step = {
            "success": False,
            "stdout": "",
            "stderr": "error: external command 'curl' exited with code 7\nerror: Reservoir lookup failed",
            "timed_out": False,
        }

        self.assertEqual(bootstrap_proofs.classify_step_failure(step), "network")

    def test_cache_failure_becomes_warning_when_compiled_libs_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shared_root = workspace / "shared_workspace"
            proofs_dir = shared_root / "proofs"
            proofs_dir.mkdir(parents=True)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:stable", encoding="utf-8")
            (proofs_dir / "lakefile.toml").write_text("[package]\nname = \"Demo\"\n", encoding="utf-8")
            (proofs_dir / "lake-manifest.json").write_text("{}", encoding="utf-8")
            lib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
            lib_dir.mkdir(parents=True)
            (lib_dir / "Mathlib.olean").write_text("", encoding="utf-8")
            mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
            mathlib_dir.mkdir(parents=True, exist_ok=True)

            args = Namespace(
                workspace=str(workspace),
                scope="local",
                proofs_dir="proofs",
                target="search",
                name=None,
                skip_update=False,
                skip_cache=False,
                build_mathlib=False,
                skip_verify=True,
                timeout_seconds=5,
                json=True,
            )

            stdout = io.StringIO()
            with (
                patch.object(bootstrap_proofs, "parse_args", return_value=args),
                patch.object(bootstrap_proofs, "find_lake", return_value=workspace / "lake.exe"),
                patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared_root),
                patch.object(bootstrap_proofs, "writability_error", return_value=None),
                patch.object(
                    bootstrap_proofs,
                    "subprocess_env_for_tool",
                    return_value={"PATH": "", "HOME": str(workspace), "ELAN_HOME": str(workspace / ".elan")},
                ),
                patch.object(bootstrap_proofs, "add_git_safe_directories", side_effect=lambda env, directories: env),
                patch.object(
                    bootstrap_proofs,
                    "discover_package_lib_dirs",
                    side_effect=[[], [lib_dir], [lib_dir]],
                ),
                patch.object(
                    bootstrap_proofs,
                    "run_command",
                    return_value={
                        "command": ["lake", "exe", "cache", "get"],
                        "cwd": str(proofs_dir),
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "proofwidgets cache-prune failure",
                        "success": False,
                        "timed_out": False,
                        "timeout_seconds": 5,
                        "duration_ms": 12,
                    },
                ),
                patch("sys.stdout", stdout),
            ):
                exit_code = bootstrap_proofs.main()

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["success"])
            self.assertEqual(payload["status"], "success")
            self.assertTrue(payload["warnings"])
            self.assertFalse(payload["next_steps"])

    def test_main_repairs_mismatched_mathlib_checkout_and_repins_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shared_root = workspace / "shared_workspace"
            proofs_dir = shared_root / "proofs"
            proofs_dir.mkdir(parents=True)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:4.29.0", encoding="utf-8")
            (proofs_dir / "lakefile.toml").write_text(
                '\n'.join(
                    [
                        'name = "SharedWorkspaceProofs"',
                        "",
                        "[[require]]",
                        'name = "mathlib"',
                        'scope = "leanprover-community"',
                        'rev = "master"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            stale_mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib"
            (stale_mathlib_dir / "Mathlib").mkdir(parents=True)
            (stale_mathlib_dir / "lean-toolchain").write_text("leanprover/lean4:v4.30.0-rc1", encoding="utf-8")
            (proofs_dir / "lake-manifest.json").write_text("{}", encoding="utf-8")

            args = Namespace(
                workspace=str(workspace),
                scope="shared",
                proofs_dir="proofs",
                target="search",
                name=None,
                skip_update=False,
                skip_cache=False,
                build_mathlib=False,
                skip_verify=True,
                timeout_seconds=5,
                json=True,
            )

            def fake_run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, object]:
                if command[-1] == "update":
                    refreshed_mathlib = proofs_dir / ".lake" / "packages" / "mathlib"
                    (refreshed_mathlib / "Mathlib").mkdir(parents=True, exist_ok=True)
                    (refreshed_mathlib / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "updated",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                if command[-3:] == ["exe", "cache", "get"]:
                    lib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
                    lib_dir.mkdir(parents=True, exist_ok=True)
                    (lib_dir / "Mathlib.olean").write_text("", encoding="utf-8")
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "cached",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                raise AssertionError(f"Unexpected bootstrap command: {command}")

            stdout = io.StringIO()
            with (
                patch.object(bootstrap_proofs, "parse_args", return_value=args),
                patch.object(bootstrap_proofs, "find_lake", return_value=workspace / "lake.exe"),
                patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared_root),
                patch.object(bootstrap_proofs, "writability_error", return_value=None),
                patch.object(
                    bootstrap_proofs,
                    "subprocess_env_for_tool",
                    return_value={"PATH": "", "HOME": str(workspace), "ELAN_HOME": str(workspace / ".elan")},
                ),
                patch.object(bootstrap_proofs, "add_git_safe_directories", side_effect=lambda env, directories: env),
                patch.object(bootstrap_proofs, "run_command", side_effect=fake_run_command),
                patch("sys.stdout", stdout),
            ):
                exit_code = bootstrap_proofs.main()

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["success"])
            self.assertEqual(payload["expected_mathlib_revision"], "v4.29.0")
            self.assertTrue(any("Normalized the shared Lean toolchain" in warning for warning in payload["warnings"]))
            self.assertTrue(any("Pinned mathlib" in warning for warning in payload["warnings"]))
            self.assertTrue(any("Removed the cached mathlib checkout" in warning for warning in payload["warnings"]))
            self.assertTrue((proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib").exists())
            self.assertEqual((proofs_dir / "lean-toolchain").read_text(encoding="utf-8").strip(), "leanprover/lean4:v4.29.0")
            self.assertIn('rev = "v4.29.0"', (proofs_dir / "lakefile.toml").read_text(encoding="utf-8"))
            self.assertFalse((proofs_dir / "lake-manifest.json").exists())
            step_names = [step["name"] for step in payload["steps"]]
            self.assertEqual(step_names, ["lake update", "lake exe cache get"])

    def test_main_runs_update_when_manifest_missing_even_if_mathlib_sources_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shared_root = workspace / "shared_workspace"
            proofs_dir = shared_root / "proofs"
            proofs_dir.mkdir(parents=True)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:4.29.0", encoding="utf-8")
            (proofs_dir / "lakefile.toml").write_text(
                '\n'.join(
                    [
                        'name = "SharedWorkspaceProofs"',
                        "",
                        "[[require]]",
                        'name = "mathlib"',
                        'scope = "leanprover-community"',
                        'rev = "v4.29.0"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib"
            (mathlib_dir / "Mathlib").mkdir(parents=True)
            (mathlib_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")

            args = Namespace(
                workspace=str(workspace),
                scope="shared",
                proofs_dir="proofs",
                target="search",
                name=None,
                skip_update=False,
                skip_cache=False,
                build_mathlib=False,
                skip_verify=True,
                timeout_seconds=5,
                json=True,
            )

            def fake_run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, object]:
                if command[-1] == "update":
                    (proofs_dir / "lake-manifest.json").write_text("{}", encoding="utf-8")
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "updated",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                if command[-3:] == ["exe", "cache", "get"]:
                    lib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
                    lib_dir.mkdir(parents=True, exist_ok=True)
                    (lib_dir / "Mathlib.olean").write_text("", encoding="utf-8")
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "cached",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                raise AssertionError(f"Unexpected bootstrap command: {command}")

            stdout = io.StringIO()
            with (
                patch.object(bootstrap_proofs, "parse_args", return_value=args),
                patch.object(bootstrap_proofs, "find_lake", return_value=workspace / "lake.exe"),
                patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared_root),
                patch.object(bootstrap_proofs, "writability_error", return_value=None),
                patch.object(
                    bootstrap_proofs,
                    "subprocess_env_for_tool",
                    return_value={"PATH": "", "HOME": str(workspace), "ELAN_HOME": str(workspace / ".elan")},
                ),
                patch.object(bootstrap_proofs, "add_git_safe_directories", side_effect=lambda env, directories: env),
                patch.object(bootstrap_proofs, "run_command", side_effect=fake_run_command),
                patch("sys.stdout", stdout),
            ):
                exit_code = bootstrap_proofs.main()

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["success"])
            self.assertTrue((proofs_dir / "lake-manifest.json").exists())
            step_names = [step["name"] for step in payload["steps"]]
            self.assertEqual(step_names, ["lake update", "lake exe cache get"])

    def test_main_retries_update_after_package_state_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shared_root = workspace / "shared_workspace"
            proofs_dir = shared_root / "proofs"
            proofs_dir.mkdir(parents=True)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lakefile.toml").write_text(
                '\n'.join(
                    [
                        'name = "SharedWorkspaceProofs"',
                        "",
                        "[[require]]",
                        'name = "mathlib"',
                        'scope = "leanprover-community"',
                        'rev = "v4.29.0"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            stale_mathlib = proofs_dir / ".lake" / "packages" / "mathlib"
            (stale_mathlib / "Mathlib").mkdir(parents=True)
            (stale_mathlib / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")

            args = Namespace(
                workspace=str(workspace),
                scope="shared",
                proofs_dir="proofs",
                target="search",
                name=None,
                skip_update=False,
                skip_cache=False,
                build_mathlib=False,
                skip_verify=True,
                timeout_seconds=5,
                json=True,
            )
            call_count = {"update": 0}

            def fake_run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, object]:
                if command[-1] == "update":
                    call_count["update"] += 1
                    if call_count["update"] == 1:
                        return {
                            "command": command,
                            "cwd": str(cwd),
                            "returncode": 1,
                            "stdout": "",
                            "stderr": "package configuration has errors",
                            "success": False,
                            "timed_out": False,
                            "timeout_seconds": timeout_seconds,
                            "duration_ms": 10,
                        }
                    refreshed_mathlib = proofs_dir / ".lake" / "packages" / "mathlib"
                    (refreshed_mathlib / "Mathlib").mkdir(parents=True, exist_ok=True)
                    (refreshed_mathlib / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
                    (proofs_dir / "lake-manifest.json").write_text("{}", encoding="utf-8")
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "updated",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                if command[-3:] == ["exe", "cache", "get"]:
                    lib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
                    lib_dir.mkdir(parents=True, exist_ok=True)
                    (lib_dir / "Mathlib.olean").write_text("", encoding="utf-8")
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "cached",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                raise AssertionError(f"Unexpected bootstrap command: {command}")

            stdout = io.StringIO()
            with (
                patch.object(bootstrap_proofs, "parse_args", return_value=args),
                patch.object(bootstrap_proofs, "find_lake", return_value=workspace / "lake.exe"),
                patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared_root),
                patch.object(bootstrap_proofs, "writability_error", return_value=None),
                patch.object(
                    bootstrap_proofs,
                    "subprocess_env_for_tool",
                    return_value={"PATH": "", "HOME": str(workspace), "ELAN_HOME": str(workspace / ".elan")},
                ),
                patch.object(bootstrap_proofs, "add_git_safe_directories", side_effect=lambda env, directories: env),
                patch.object(bootstrap_proofs, "run_command", side_effect=fake_run_command),
                patch("sys.stdout", stdout),
            ):
                exit_code = bootstrap_proofs.main()

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["success"])
            self.assertTrue(any("retried from a fresh clone" in warning for warning in payload["warnings"]))
            step_names = [step["name"] for step in payload["steps"]]
            self.assertEqual(step_names, ["lake update", "lake update (retry)", "lake exe cache get"])

    def test_main_search_target_does_not_build_mathlib_when_artifact_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shared_root = workspace / "shared_workspace"
            proofs_dir = shared_root / "proofs"
            proofs_dir.mkdir(parents=True)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lakefile.toml").write_text(
                '\n'.join(
                    [
                        'name = "SharedWorkspaceProofs"',
                        "",
                        "[[require]]",
                        'name = "mathlib"',
                        'scope = "leanprover-community"',
                        'rev = "v4.29.0"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib"
            lib_dir = mathlib_dir / ".lake" / "build" / "lib" / "lean"
            lib_dir.mkdir(parents=True)
            (mathlib_dir / "Mathlib").mkdir(parents=True, exist_ok=True)
            (mathlib_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lake-manifest.json").write_text("{}", encoding="utf-8")

            args = Namespace(
                workspace=str(workspace),
                scope="shared",
                proofs_dir="proofs",
                target="search",
                name=None,
                skip_update=False,
                skip_cache=False,
                build_mathlib=False,
                skip_verify=True,
                timeout_seconds=5,
                json=True,
            )

            def fake_run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, object]:
                if command[-3:] == ["exe", "cache", "get"]:
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "cached",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                raise AssertionError(f"Unexpected bootstrap command: {command}")

            stdout = io.StringIO()
            with (
                patch.object(bootstrap_proofs, "parse_args", return_value=args),
                patch.object(bootstrap_proofs, "find_lake", return_value=workspace / "lake.exe"),
                patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared_root),
                patch.object(bootstrap_proofs, "writability_error", return_value=None),
                patch.object(
                    bootstrap_proofs,
                    "subprocess_env_for_tool",
                    return_value={"PATH": "", "HOME": str(workspace), "ELAN_HOME": str(workspace / ".elan")},
                ),
                patch.object(bootstrap_proofs, "add_git_safe_directories", side_effect=lambda env, directories: env),
                patch.object(bootstrap_proofs, "run_command", side_effect=fake_run_command),
                patch("sys.stdout", stdout),
            ):
                exit_code = bootstrap_proofs.main()

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["success"])
            self.assertFalse(payload["postconditions"]["mathlib_artifact_exists"])
            self.assertEqual(payload["readiness_level"], "search-ready")
            step_names = [step["name"] for step in payload["steps"]]
            self.assertEqual(step_names, ["lake exe cache get"])

    def test_main_verify_target_builds_mathlib_when_artifact_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shared_root = workspace / "shared_workspace"
            proofs_dir = shared_root / "proofs"
            proofs_dir.mkdir(parents=True)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lakefile.toml").write_text(
                '\n'.join(
                    [
                        'name = "SharedWorkspaceProofs"',
                        "",
                        "[[require]]",
                        'name = "mathlib"',
                        'scope = "leanprover-community"',
                        'rev = "v4.29.0"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib"
            lib_dir = mathlib_dir / ".lake" / "build" / "lib" / "lean"
            lib_dir.mkdir(parents=True)
            (mathlib_dir / "Mathlib").mkdir(parents=True, exist_ok=True)
            (mathlib_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lake-manifest.json").write_text("{}", encoding="utf-8")

            args = Namespace(
                workspace=str(workspace),
                scope="shared",
                proofs_dir="proofs",
                target="verify",
                name=None,
                skip_update=False,
                skip_cache=False,
                build_mathlib=False,
                skip_verify=True,
                timeout_seconds=5,
                json=True,
            )

            def fake_run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, object]:
                if command[-3:] == ["exe", "cache", "get"]:
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "cached",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                if command[-2:] == ["build", "Mathlib"]:
                    (lib_dir / "Mathlib.olean").write_text("", encoding="utf-8")
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "built",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                raise AssertionError(f"Unexpected bootstrap command: {command}")

            stdout = io.StringIO()
            with (
                patch.object(bootstrap_proofs, "parse_args", return_value=args),
                patch.object(bootstrap_proofs, "find_lake", return_value=workspace / "lake.exe"),
                patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared_root),
                patch.object(bootstrap_proofs, "writability_error", return_value=None),
                patch.object(
                    bootstrap_proofs,
                    "subprocess_env_for_tool",
                    return_value={"PATH": "", "HOME": str(workspace), "ELAN_HOME": str(workspace / ".elan")},
                ),
                patch.object(bootstrap_proofs, "add_git_safe_directories", side_effect=lambda env, directories: env),
                patch.object(bootstrap_proofs, "run_command", side_effect=fake_run_command),
                patch("sys.stdout", stdout),
            ):
                exit_code = bootstrap_proofs.main()

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["success"])
            self.assertTrue(payload["postconditions"]["mathlib_artifact_exists"])
            self.assertEqual(payload["readiness_level"], "verification-ready")
            step_names = [step["name"] for step in payload["steps"]]
            self.assertEqual(step_names, ["lake exe cache get", "lake build Mathlib"])

    def test_main_verify_target_rebuilds_when_smoke_check_fails_with_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shared_root = workspace / "shared_workspace"
            proofs_dir = shared_root / "proofs"
            proofs_dir.mkdir(parents=True)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lakefile.toml").write_text(
                '\n'.join(
                    [
                        'name = "SharedWorkspaceProofs"',
                        "",
                        "[[require]]",
                        'name = "mathlib"',
                        'scope = "leanprover-community"',
                        'rev = "v4.29.0"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib"
            lib_dir = mathlib_dir / ".lake" / "build" / "lib" / "lean"
            lib_dir.mkdir(parents=True)
            (lib_dir / "Mathlib.olean").write_text("", encoding="utf-8")
            (mathlib_dir / "Mathlib").mkdir(parents=True, exist_ok=True)
            (mathlib_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lake-manifest.json").write_text("{}", encoding="utf-8")

            args = Namespace(
                workspace=str(workspace),
                scope="shared",
                proofs_dir="proofs",
                target="verify",
                name=None,
                skip_update=False,
                skip_cache=False,
                build_mathlib=False,
                skip_verify=True,
                timeout_seconds=5,
                json=True,
            )

            def fake_run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, object]:
                if command[-2:] == ["build", "Mathlib"]:
                    return {
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": 0,
                        "stdout": "rebuilt",
                        "stderr": "",
                        "success": True,
                        "timed_out": False,
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": 10,
                    }
                raise AssertionError(f"Unexpected bootstrap command: {command}")

            stdout = io.StringIO()
            with (
                patch.object(bootstrap_proofs, "parse_args", return_value=args),
                patch.object(bootstrap_proofs, "find_lake", return_value=workspace / "lake.exe"),
                patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared_root),
                patch.object(bootstrap_proofs, "writability_error", return_value=None),
                patch.object(
                    bootstrap_proofs,
                    "subprocess_env_for_tool",
                    return_value={"PATH": "", "HOME": str(workspace), "ELAN_HOME": str(workspace / ".elan")},
                ),
                patch.object(bootstrap_proofs, "add_git_safe_directories", side_effect=lambda env, directories: env),
                patch.object(
                    bootstrap_proofs,
                    "run_verification_readiness_check",
                    return_value={"checked": True, "success": False, "error": "missing nested olean"},
                ),
                patch.object(bootstrap_proofs, "run_command", side_effect=fake_run_command),
                patch("sys.stdout", stdout),
            ):
                exit_code = bootstrap_proofs.main()

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["success"])
            self.assertTrue(any("force `lake build Mathlib`" in warning for warning in payload["warnings"]))
            step_names = [step["name"] for step in payload["steps"]]
            self.assertEqual(step_names, ["lake build Mathlib"])

    def test_main_verify_target_failed_lean_check_downgrades_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shared_root = workspace / "shared_workspace"
            proofs_dir = shared_root / "proofs"
            proofs_dir.mkdir(parents=True)
            (proofs_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lakefile.toml").write_text(
                '\n'.join(
                    [
                        'name = "SharedWorkspaceProofs"',
                        "",
                        "[[require]]",
                        'name = "mathlib"',
                        'scope = "leanprover-community"',
                        'rev = "v4.29.0"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib"
            lib_dir = mathlib_dir / ".lake" / "build" / "lib" / "lean"
            lib_dir.mkdir(parents=True)
            (lib_dir / "Mathlib.olean").write_text("", encoding="utf-8")
            (mathlib_dir / "Mathlib").mkdir(parents=True, exist_ok=True)
            (mathlib_dir / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
            (proofs_dir / "lake-manifest.json").write_text("{}", encoding="utf-8")
            (proofs_dir / "ProofScratch.lean").write_text("import Mathlib\n", encoding="utf-8")

            args = Namespace(
                workspace=str(workspace),
                scope="shared",
                proofs_dir="proofs",
                target="verify",
                name=None,
                skip_update=False,
                skip_cache=False,
                build_mathlib=False,
                skip_verify=False,
                timeout_seconds=5,
                json=True,
            )

            stdout = io.StringIO()
            with (
                patch.object(bootstrap_proofs, "parse_args", return_value=args),
                patch.object(bootstrap_proofs, "find_lake", return_value=workspace / "lake.exe"),
                patch.object(bootstrap_proofs, "shared_workspace_root", return_value=shared_root),
                patch.object(bootstrap_proofs, "writability_error", return_value=None),
                patch.object(
                    bootstrap_proofs,
                    "subprocess_env_for_tool",
                    return_value={"PATH": "", "HOME": str(workspace), "ELAN_HOME": str(workspace / ".elan")},
                ),
                patch.object(bootstrap_proofs, "add_git_safe_directories", side_effect=lambda env, directories: env),
                patch.object(
                    bootstrap_proofs,
                    "run_verification_readiness_check",
                    return_value={"checked": True, "success": True, "verification_method": "lake env lean"},
                ),
                patch.object(bootstrap_proofs, "run_command") as run_command_mock,
                patch(
                    "bootstrap_proofs.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=["python", "lean_check.py"],
                        returncode=0,
                        stdout=json.dumps({"success": False, "verification_method": None}),
                        stderr="",
                    ),
                ),
                patch("sys.stdout", stdout),
            ):
                exit_code = bootstrap_proofs.main()

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 5)
            self.assertFalse(payload["success"])
            self.assertEqual(payload["status"], "partial_success")
            self.assertFalse(payload["postconditions"]["ready_for_verification"])
            self.assertFalse(payload["postconditions"]["verification_success"])
            run_command_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
