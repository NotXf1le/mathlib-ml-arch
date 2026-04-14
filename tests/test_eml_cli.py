from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).parent.parent
WORKSPACE_ROOT = next(candidate for candidate in [PLUGIN_ROOT.parent.parent, *PLUGIN_ROOT.parents] if (candidate / "proofs").exists())
SKILL_SCRIPT_DIR = PLUGIN_ROOT / "skills" / "mathlib-ml-arch" / "scripts"
if str(SKILL_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT_DIR))

from common import find_lake, find_lean, resolve_proofs_workspace  # noqa: E402


def runtime_ready() -> bool:
    lake = find_lake()
    root, _ = resolve_proofs_workspace(WORKSPACE_ROOT, "auto")
    if root is None or lake is None or find_lean(lake) is None:
        return False

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    smoke = subprocess.run(
        [
            sys.executable,
            str(PLUGIN_ROOT / "scripts" / "lean_check.py"),
            "--workspace",
            str(WORKSPACE_ROOT),
            "--mode",
            "direct",
            "--timeout-seconds",
            "5",
            "--json",
        ],
        cwd=WORKSPACE_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        payload = json.loads(smoke.stdout or "{}")
    except json.JSONDecodeError:
        return False
    return bool(payload.get("success"))


class EmlCliTests(unittest.TestCase):
    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=WORKSPACE_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

    def test_eml_normalize_cli_writes_valid_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_script(
                PLUGIN_ROOT / "scripts" / "eml_normalize.py",
                "--formula",
                "exp(x)",
                "--output-dir",
                tmp,
                "--json",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["compile_status"], "exact")
            evidence = json.loads((Path(tmp) / "evidence.json").read_text(encoding="utf-8"))
            self.assertTrue(
                evidence[0]["claim_label"] in {"Partial formal support", "Formal support", "No direct formal support found in mathlib"}
            )

    @unittest.skipUnless(runtime_ready(), "Lean or proofs workspace is unavailable in this environment.")
    def test_eml_verify_cli_runs_real_lean_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_script(
                PLUGIN_ROOT / "scripts" / "eml_verify.py",
                "--formula",
                "exp(x)",
                "--workspace",
                str(WORKSPACE_ROOT),
                "--output-dir",
                tmp,
                "--lean-mode",
                "direct",
                "--timeout-seconds",
                "30",
                "--json",
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["verified_in_lean"], msg=result.stdout)
            evidence = json.loads((Path(tmp) / "evidence.json").read_text(encoding="utf-8"))
            self.assertTrue(evidence[0]["verified_in_lean"])


if __name__ == "__main__":
    unittest.main()
