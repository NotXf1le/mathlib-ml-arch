from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import configure_stdout, requested_workspace_root
from eml_pipeline import (
    analyze_formula,
    build_evidence_record,
    boundary_mermaid,
    eml_mermaid,
    ensure_bundle_layout,
    load_existing_evidence,
    replace_record,
    report_sections,
)


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[3]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse one scalar formula into CalcLang, emit EML normalization artifacts, and track side conditions."
    )
    parser.add_argument("--formula", help="Formula string to normalize.")
    parser.add_argument("--formula-file", help="Path to a UTF-8 text file containing the formula.")
    parser.add_argument("--workspace", help="Workspace root used for default output placement.")
    parser.add_argument("--output-dir", help="Artifact bundle directory. Defaults to <workspace>/reports/eml_normalize.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    return parser.parse_args()


def resolve_formula(args: argparse.Namespace) -> str:
    if args.formula:
        return args.formula
    if args.formula_file:
        return Path(args.formula_file).expanduser().read_text(encoding="utf-8")
    raise SystemExit("Pass --formula or --formula-file.")


def default_output_dir(workspace: Path) -> Path:
    return workspace / "reports" / "eml_normalize"


def run_validator(output_dir: Path) -> dict[str, object]:
    validator = plugin_root() / "scripts" / "validate_artifact_bundle.py"
    result = subprocess.run(
        [sys.executable, str(validator), "--bundle-dir", str(output_dir), "--json"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"valid": False, "issues": ["Validator returned unreadable output."], "stdout": result.stdout, "stderr": result.stderr}
    payload["validator_exit_code"] = result.returncode
    return payload


def print_human(payload: dict[str, object]) -> None:
    print(f"formula: {payload['formula']}")
    print(f"output: {payload['output_dir']}")
    print(f"parse status: {payload['parse_status']}")
    print(f"compile status: {payload['compile_status']}")
    print(f"normalized: {payload.get('normalized_formula') or 'n/a'}")
    if payload.get("validation"):
        print(f"bundle valid: {payload['validation'].get('valid', False)}")


def main() -> int:
    configure_stdout()
    args = parse_args()
    workspace = requested_workspace_root(args.workspace)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else default_output_dir(workspace)
    formula = resolve_formula(args)
    analysis = analyze_formula(formula)
    layout = ensure_bundle_layout(output_dir)

    parse_payload = analysis["parse"]
    parse_status = str(parse_payload["status"])

    formula_payload = {
        "formula": formula,
        "parse": parse_payload,
        "normalized_expression": analysis.get("normalized_expression"),
        "normalized_text": analysis.get("normalized_text"),
    }
    layout["formula_json"].write_text(json.dumps(formula_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    evidence_record: dict[str, object]
    report_text: str
    compile_status = "unsupported"
    if parse_status == "ok":
        normalized_expr = analysis["expression_obj"]
        compile_result = analysis["compile"]
        compile_status = str(compile_result["status"])
        eml_payload = {
            "formula": formula,
            "binding_name": analysis.get("binding_name"),
            "normalized_text": analysis["normalized_text"],
            "compile": compile_result,
            "side_conditions": analysis["side_conditions"],
            "proof_obligations": analysis["proof_obligations"],
        }
        layout["eml_json"].write_text(json.dumps(eml_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        layout["eml_tree_mmd"].write_text(eml_mermaid(normalized_expr), encoding="utf-8")
        layout["boundary_graph_mmd"].write_text(boundary_mermaid(analysis["normalized_text"], analysis["side_conditions"]), encoding="utf-8")
        evidence_record = build_evidence_record(
            formula,
            analysis.get("binding_name"),
            normalized_expr,
            compile_result,
            analysis["side_conditions"],
            verified_in_lean=False,
            verification_method="not_run",
        )
        report_text = report_sections(
            formula,
            analysis.get("binding_name"),
            normalized_expr,
            compile_result,
            analysis["side_conditions"],
            verified_in_lean=False,
            verification_method="not_run",
        )
    else:
        layout["eml_json"].write_text(
            json.dumps({"formula": formula, "parse": parse_payload, "compile": {"status": "unsupported"}}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        layout["eml_tree_mmd"].write_text('graph TD\n  root["parse failed"]', encoding="utf-8")
        layout["boundary_graph_mmd"].write_text('graph TD\n  root["no boundary graph available"]', encoding="utf-8")
        evidence_record = {
            "name": "parse_error",
            "import_path": "generated::eml_normalize",
            "plain_language_meaning": f"Failed to parse formula `{formula.strip()}`.",
            "supported_subclaim": "No structured formal claim was extracted because parsing failed.",
            "unsupported_boundary": "; ".join(parse_payload["errors"]),
            "claim_label": "Empirical gap",
            "verified_in_lean": False,
            "verification_method": "not_run",
            "side_conditions": [],
        }
        report_text = """## Proposed architecture

- Parse failed for the requested formula, so no CalcLang normalization was produced.

## Formal evidence from mathlib

- No formal evidence was attempted because parsing failed.

## Engineering inference built on top of formal facts

- The parser is intentionally conservative in v1 and does not guess around ambiguous syntax.

## Gaps requiring benchmarks or papers

- Unsupported or ambiguous syntax must be rewritten into an explicit scalar formula before EML normalization can proceed.

## Risks

- Guessing a parse would create false formal support, which the plugin explicitly avoids.
"""

    records = replace_record(load_existing_evidence(layout["evidence_json"]), evidence_record)
    layout["evidence_json"].write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    layout["report_md"].write_text(report_text, encoding="utf-8")
    layout["session_log_json"].write_text(
        json.dumps(
            {
                "phase": "eml_normalize",
                "formula": formula,
                "parse_status": parse_status,
                "compile_status": compile_status,
                "lean_verification": {"attempted": False, "verification_method": "not_run"},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    validation = run_validator(output_dir)
    payload = {
        "formula": formula,
        "output_dir": str(output_dir),
        "parse_status": parse_status,
        "compile_status": compile_status,
        "normalized_formula": analysis.get("normalized_text"),
        "validation": validation,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(payload)

    return 0 if validation.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
