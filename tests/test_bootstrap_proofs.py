from __future__ import annotations

import io
import json
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
            lib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
            lib_dir.mkdir(parents=True)
            mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
            mathlib_dir.mkdir(parents=True, exist_ok=True)

            args = Namespace(
                workspace=str(workspace),
                scope="local",
                proofs_dir="proofs",
                name=None,
                skip_update=False,
                skip_cache=False,
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


if __name__ == "__main__":
    unittest.main()
