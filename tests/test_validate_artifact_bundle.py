from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True

ROOT_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(ROOT_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_SCRIPTS_DIR))

from validate_artifact_bundle import validate_evidence, validate_report  # noqa: E402


VALID_REPORT = """## Proposed architecture

- Example

## Formal evidence from mathlib

- Example

## Engineering inference built on top of formal facts

- Example

## Gaps requiring benchmarks or papers

- Example

## Risks

- Example
"""


class ValidateArtifactBundleTests(unittest.TestCase):
    def make_record(self) -> dict[str, object]:
        return {
            "name": "demo",
            "import_path": "generated::test",
            "plain_language_meaning": "Demo record.",
            "supported_subclaim": "Demo subclaim.",
            "unsupported_boundary": "Demo boundary.",
            "claim_label": "Formal support",
            "verified_in_lean": True,
            "verification_method": "lake env lean",
            "side_conditions": [
                {"kind": "domain", "condition": "x != 0", "status": "required"},
            ],
        }

    def test_valid_bundle_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "report.md").write_text(VALID_REPORT, encoding="utf-8")
            (bundle / "evidence.json").write_text(
                json.dumps([self.make_record()], indent=2),
                encoding="utf-8",
            )

            report_issues, _ = validate_report(bundle / "report.md")
            evidence_issues, _ = validate_evidence(bundle / "evidence.json")

            self.assertFalse(report_issues)
            self.assertFalse(evidence_issues)

    def test_missing_verification_metadata_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            bad = self.make_record()
            bad.pop("verified_in_lean")
            (bundle / "evidence.json").write_text(json.dumps([bad], indent=2), encoding="utf-8")

            evidence_issues, _ = validate_evidence(bundle / "evidence.json")

            self.assertTrue(any("verified_in_lean" in issue for issue in evidence_issues))

    def test_formal_support_requires_verified_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            bad = self.make_record()
            bad["verified_in_lean"] = False
            (bundle / "evidence.json").write_text(json.dumps([bad], indent=2), encoding="utf-8")

            evidence_issues, _ = validate_evidence(bundle / "evidence.json")

            self.assertTrue(any("Formal support" in issue for issue in evidence_issues))


if __name__ == "__main__":
    unittest.main()
