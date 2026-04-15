from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.dont_write_bytecode = True

SKILL_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "mathlib-ml-arch" / "scripts"
if str(SKILL_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT_DIR))

import doctor  # noqa: E402


class DoctorTests(unittest.TestCase):
    def test_build_payload_exposes_verify_readiness_and_smoke_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            proofs_dir = workspace / "proofs"
            mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
            mathlib_dir.mkdir(parents=True)
            (proofs_dir / "ProofScratch.lean").write_text("import Mathlib\n", encoding="utf-8")

            workspace_status = {
                "project_toolchain": "leanprover/lean4:v4.29.0",
                "mathlib_toolchain": "leanprover/lean4:v4.29.0",
                "toolchain_compatible": True,
                "mathlib_artifact_exists": True,
                "ready_for_search": True,
                "ready_for_verification": True,
                "readiness_level": "verification-ready",
                "verification_smoke": {
                    "checked": True,
                    "success": True,
                    "verification_method": "lake env lean",
                },
            }

            with (
                patch.object(doctor, "find_existing_proofs_root", return_value=None),
                patch.object(doctor, "find_shared_proofs_root", return_value=workspace),
                patch.object(doctor, "shared_workspace_root", return_value=workspace),
                patch.object(doctor, "resolve_proofs_workspace", return_value=(workspace, "shared")),
                patch.object(doctor, "proofs_workspace_status", return_value=workspace_status),
                patch.object(doctor, "discover_package_lib_dirs", return_value=[]),
                patch.object(doctor, "find_lake", return_value=None),
                patch.object(doctor, "find_lean", return_value=None),
                patch.object(doctor, "find_elan", return_value=None),
                patch.object(doctor, "cached_elan_homes", return_value=[]),
            ):
                payload = doctor.build_payload(workspace, "shared")

            self.assertTrue(payload["ready_for_search"])
            self.assertTrue(payload["ready_for_verification"])
            self.assertTrue(payload["verification_smoke_checked"])
            self.assertTrue(payload["verification_smoke_success"])
            self.assertEqual(payload["verification_smoke_method"], "lake env lean")


if __name__ == "__main__":
    unittest.main()
