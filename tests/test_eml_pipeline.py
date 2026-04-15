from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "mathlib-ml-arch" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from eml_pipeline import analyze_formula, build_evidence_record, proof_file_source, report_sections  # noqa: E402


class EmlPipelineTests(unittest.TestCase):
    def test_exp_formula_compiles_exactly(self) -> None:
        analysis = analyze_formula("exp(x)")

        self.assertEqual(analysis["parse"]["status"], "ok")
        self.assertEqual(analysis["normalized_text"], "exp(x)")
        self.assertEqual(analysis["compile"]["status"], "exact")
        self.assertEqual(analysis["compile"]["eml_tree"]["kind"], "eml")
        self.assertFalse(analysis["side_conditions"])

    def test_sigmoid_desugars_and_keeps_boundary_tracking(self) -> None:
        analysis = analyze_formula("sigmoid(z)")

        self.assertEqual(analysis["parse"]["status"], "ok")
        self.assertIn("exp(-z)", analysis["normalized_text"])
        self.assertEqual(analysis["compile"]["status"], "partial")
        conditions = [item["condition"] for item in analysis["side_conditions"]]
        self.assertTrue(any("!= 0" in condition for condition in conditions))

    def test_log_sigmoid_tracks_log_side_conditions(self) -> None:
        analysis = analyze_formula("log(sigmoid(z))")

        self.assertEqual(analysis["parse"]["status"], "ok")
        conditions = analysis["side_conditions"]
        self.assertTrue(any(item["kind"] == "domain" and "!= 0" in item["condition"] for item in conditions))
        self.assertTrue(any(item["status"] == "required_for_real_output" for item in conditions))

    def test_exact_proof_source_contains_compile_sound_theorem(self) -> None:
        analysis = analyze_formula("exp(x)")
        source = proof_file_source(
            "g",
            analysis["expression_obj"],
            analysis["compile"],
            analysis["side_conditions"],
        )

        self.assertIn("theorem compile_sound", source)
        self.assertIn("def g : CalcExpr", source)

    def test_exact_unverified_formula_is_partial_support(self) -> None:
        analysis = analyze_formula("exp(x)")
        record = build_evidence_record(
            "exp(x)",
            analysis["binding_name"],
            analysis["expression_obj"],
            analysis["compile"],
            analysis["side_conditions"],
            verified_in_lean=False,
            verification_method="unavailable:no proofs workspace",
        )

        self.assertEqual(record["claim_label"], "Partial formal support")
        self.assertEqual(record["formal_verification_status"], "unavailable")
        self.assertIn("No checked root theorem was obtained", record["unsupported_boundary"])
        self.assertNotIn("No direct formal support found in mathlib", record["unsupported_boundary"])

    def test_report_marks_not_run_verification_explicitly(self) -> None:
        analysis = analyze_formula("exp(x)")
        report = report_sections(
            "exp(x)",
            analysis["binding_name"],
            analysis["expression_obj"],
            analysis["compile"],
            analysis["side_conditions"],
            verified_in_lean=False,
            verification_method="not_run",
        )

        self.assertIn("Formal verification status: `not_run`", report)
        self.assertIn("Formal verification was not run in this step.", report)
        self.assertNotIn("did not succeed", report)


if __name__ == "__main__":
    unittest.main()
