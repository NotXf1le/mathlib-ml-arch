from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import configure_stdout, requested_workspace_root
from eml_pipeline import (
    analyze_formula,
    boundary_mermaid,
    build_evidence_record,
    ensure_bundle_layout,
    load_existing_evidence,
    replace_record,
    report_sections,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract domain, branch, and totalization side conditions for one scalar formula."
    )
    parser.add_argument("--formula", help="Formula string to classify.")
    parser.add_argument("--formula-file", help="Path to a UTF-8 text file containing the formula.")
    parser.add_argument("--workspace", help="Workspace root used for default output placement.")
    parser.add_argument("--output-dir", help="Artifact bundle directory. Defaults to <workspace>/reports/boundary_classify.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    return parser.parse_args()


def resolve_formula(args: argparse.Namespace) -> str:
    if args.formula:
        return args.formula
    if args.formula_file:
        return Path(args.formula_file).expanduser().read_text(encoding="utf-8")
    raise SystemExit("Pass --formula or --formula-file.")


def default_output_dir(workspace: Path) -> Path:
    return workspace / "reports" / "boundary_classify"


def print_human(payload: dict[str, object]) -> None:
    print(f"formula: {payload['formula']}")
    print(f"output: {payload['output_dir']}")
    print(f"parse status: {payload['parse_status']}")
    print(f"side conditions: {payload['side_condition_count']}")


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
    side_conditions = analysis.get("side_conditions", [])

    if parse_status == "ok":
        normalized_expr = analysis["expression_obj"]
        compile_result = analysis["compile"]
        evidence_record = build_evidence_record(
            formula,
            analysis.get("binding_name"),
            normalized_expr,
            compile_result,
            side_conditions,
            verified_in_lean=False,
            verification_method="not_run",
        )
        report_text = report_sections(
            formula,
            analysis.get("binding_name"),
            normalized_expr,
            compile_result,
            side_conditions,
            verified_in_lean=False,
            verification_method="not_run",
        )
        boundary_graph = boundary_mermaid(analysis["normalized_text"], side_conditions)
        artifacts_payload = {
            "formula": formula,
            "binding_name": analysis.get("binding_name"),
            "normalized_text": analysis["normalized_text"],
            "side_conditions": side_conditions,
            "compile_status": compile_result["status"],
        }
    else:
        evidence_record = {
            "name": "parse_error",
            "import_path": "generated::boundary_classify",
            "plain_language_meaning": f"Failed to parse formula `{formula.strip()}`.",
            "supported_subclaim": "No boundary classification was produced because parsing failed.",
            "unsupported_boundary": "; ".join(parse_payload["errors"]),
            "claim_label": "Empirical gap",
            "verified_in_lean": False,
            "verification_method": "not_run",
            "side_conditions": [],
        }
        report_text = """## Proposed architecture

- Boundary classification could not start because parsing failed.

## Formal evidence from mathlib

- No formal evidence was attempted.

## Engineering inference built on top of formal facts

- v1 boundary classification is conservative and only operates on explicit scalar formulas.

## Gaps requiring benchmarks or papers

- Rephrase the formula into supported scalar syntax before retrying.

## Risks

- Forcing a guessed parse would create false domain or branch assumptions.
"""
        boundary_graph = 'graph TD\n  root["parse failed"]'
        artifacts_payload = {"formula": formula, "parse": parse_payload, "side_conditions": []}

    layout["formula_json"].write_text(json.dumps({"formula": formula, "parse": parse_payload}, indent=2, ensure_ascii=False), encoding="utf-8")
    layout["eml_json"].write_text(json.dumps(artifacts_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    layout["boundary_graph_mmd"].write_text(boundary_graph, encoding="utf-8")
    layout["report_md"].write_text(report_text, encoding="utf-8")

    records = replace_record(load_existing_evidence(layout["evidence_json"]), evidence_record)
    layout["evidence_json"].write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    layout["session_log_json"].write_text(
        json.dumps(
            {"phase": "boundary_classify", "formula": formula, "parse_status": parse_status, "side_condition_count": len(side_conditions)},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = {
        "formula": formula,
        "output_dir": str(output_dir),
        "parse_status": parse_status,
        "side_condition_count": len(side_conditions),
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
