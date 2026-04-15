from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from common import configure_stdout, requested_workspace_root, resolve_proofs_workspace
from common import ensure_shared_proofs_workspace
from eml_pipeline import (
    analyze_formula,
    build_evidence_record,
    boundary_mermaid,
    eml_mermaid,
    ensure_bundle_layout,
    load_existing_evidence,
    proof_file_source,
    replace_record,
    report_sections,
)


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[3]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate one Lean proof scratch file for an EML normalization attempt and run lean_check.py."
    )
    parser.add_argument("--formula", help="Formula string to verify.")
    parser.add_argument("--formula-file", help="Path to a UTF-8 text file containing the formula.")
    parser.add_argument("--workspace", help="Workspace root used to derive shared-proof scratch naming and bundle output.")
    parser.add_argument("--output-dir", help="Artifact bundle directory. Defaults to <workspace>/reports/eml_verify.")
    parser.add_argument(
        "--scope",
        choices=["auto", "local", "shared"],
        default="auto",
        help="Which proofs workspace to use. The plugin is shared-workspace-only; `auto`, `shared`, and legacy `local` all resolve to the shared CODEX_HOME cache.",
    )
    parser.add_argument(
        "--scratch-file",
        default="ProofScratch.lean",
        help="Lean target inside proofs/ or an absolute Lean file path. The default is automatically namespaced per workspace in shared-workspace mode.",
    )
    parser.add_argument("--lean-mode", choices=["auto", "lake", "direct"], default="auto", help="Verification mode forwarded to lean_check.py.")
    parser.add_argument("--timeout-seconds", type=int, default=60, help="Per-command timeout forwarded to lean_check.py.")
    parser.add_argument(
        "--bootstrap-timeout-seconds",
        type=int,
        default=60,
        help="Timeout used when the script needs to bootstrap the shared proofs workspace automatically.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    return parser.parse_args()


def resolve_formula(args: argparse.Namespace) -> str:
    if args.formula:
        return args.formula
    if args.formula_file:
        return Path(args.formula_file).expanduser().read_text(encoding="utf-8")
    raise SystemExit("Pass --formula or --formula-file.")


def default_output_dir(workspace: Path) -> Path:
    return workspace / "reports" / "eml_verify"


def default_scratch_relpath(workspace: Path) -> Path:
    slug_source = workspace.name or "workspace"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", slug_source).strip("_") or "workspace"
    digest = hashlib.sha1(str(workspace).encode("utf-8")).hexdigest()[:10]
    return Path("scratch") / f"ProofScratch_{slug}_{digest}.lean"


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


def run_lean_check(workspace_root: Path, target_file: Path, mode: str, timeout_seconds: int) -> dict[str, object]:
    lean_check = Path(__file__).with_name("lean_check.py")
    result = subprocess.run(
        [
            sys.executable,
            str(lean_check),
            "--workspace",
            str(workspace_root),
            "--file",
            str(target_file),
            "--mode",
            mode,
            "--timeout-seconds",
            str(timeout_seconds),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"success": False, "stdout": result.stdout, "stderr": result.stderr, "error": "lean_check.py returned unreadable output."}
    payload["lean_check_exit_code"] = result.returncode
    return payload


def print_human(payload: dict[str, object]) -> None:
    print(f"formula: {payload['formula']}")
    print(f"output: {payload['output_dir']}")
    print(f"parse status: {payload['parse_status']}")
    print(f"compile status: {payload['compile_status']}")
    print(f"verified in lean: {payload['verified_in_lean']}")
    print(f"verification method: {payload['verification_method']}")
    if payload.get("validation"):
        print(f"bundle valid: {payload['validation'].get('valid', False)}")


def inferred_verification_method(payload: dict[str, object]) -> str:
    if payload.get("verification_method"):
        return str(payload["verification_method"])
    methods = payload.get("methods")
    if isinstance(methods, list) and methods:
        first = methods[0]
        if isinstance(first, dict):
            name = str(first.get("name") or "attempted")
            if first.get("timed_out"):
                return f"{name} (timed out)"
            return name
    return "unverified"


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
    compile_status = "unsupported"
    lean_payload: dict[str, object] = {"success": False, "verification_method": "not_run"}
    verified_in_lean = False
    verification_method = "not_run"
    scratch_target: str | None = None
    bootstrap_payload: dict[str, object] | None = None

    layout["formula_json"].write_text(
        json.dumps(
            {"formula": formula, "parse": parse_payload, "normalized_expression": analysis.get("normalized_expression"), "normalized_text": analysis.get("normalized_text")},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if parse_status == "ok":
        normalized_expr = analysis["expression_obj"]
        compile_result = analysis["compile"]
        compile_status = str(compile_result["status"])
        side_conditions = analysis["side_conditions"]
        layout["eml_json"].write_text(
            json.dumps(
                {
                    "formula": formula,
                    "binding_name": analysis.get("binding_name"),
                    "normalized_text": analysis["normalized_text"],
                    "compile": compile_result,
                    "side_conditions": side_conditions,
                    "proof_obligations": analysis["proof_obligations"],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        layout["eml_tree_mmd"].write_text(eml_mermaid(normalized_expr), encoding="utf-8")
        layout["boundary_graph_mmd"].write_text(boundary_mermaid(analysis["normalized_text"], side_conditions), encoding="utf-8")

        proof_source = proof_file_source(analysis.get("binding_name"), normalized_expr, compile_result, side_conditions)
        proofs_root, selected_scope, workspace_status, bootstrap_payload = ensure_shared_proofs_workspace(
            workspace,
            timeout_seconds=args.bootstrap_timeout_seconds,
            require_verification=True,
        )
        if proofs_root is None or not bool(workspace_status.get("proofs_exists")):
            proofs_root, selected_scope = resolve_proofs_workspace(workspace, args.scope)
        if proofs_root is not None:
            proofs_dir = proofs_root / "proofs"
            scratch_path = Path(args.scratch_file)
            if args.scratch_file == "ProofScratch.lean":
                scratch_path = default_scratch_relpath(workspace)
            if not scratch_path.is_absolute():
                scratch_path = (proofs_dir / scratch_path).resolve()
            scratch_path.parent.mkdir(parents=True, exist_ok=True)
            scratch_path.write_text(proof_source, encoding="utf-8")
            scratch_target = str(scratch_path)
            lean_payload = run_lean_check(proofs_root, scratch_path, args.lean_mode, args.timeout_seconds)
            verification_method = inferred_verification_method(lean_payload)
            verified_in_lean = compile_status == "exact" and bool(lean_payload.get("success"))
            if compile_status != "exact" and lean_payload.get("success"):
                verification_method = f"{verification_method} (library-only)"
            lean_payload["selected_scope"] = selected_scope
        else:
            lean_payload = {
                "success": False,
                "verification_method": "unavailable:bootstrap_failed",
                "error": "No usable shared proofs workspace was found after automatic bootstrap.",
                "bootstrap": bootstrap_payload,
            }
            verification_method = str(lean_payload["verification_method"])

        evidence_record = build_evidence_record(
            formula,
            analysis.get("binding_name"),
            normalized_expr,
            compile_result,
            side_conditions,
            verified_in_lean=verified_in_lean,
            verification_method=verification_method,
        )
        report_text = report_sections(
            formula,
            analysis.get("binding_name"),
            normalized_expr,
            compile_result,
            side_conditions,
            verified_in_lean=verified_in_lean,
            verification_method=verification_method,
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
            "import_path": "generated::eml_verify",
            "plain_language_meaning": f"Failed to parse formula `{formula.strip()}`.",
            "supported_subclaim": "No EML verification attempt was made because parsing failed.",
            "unsupported_boundary": "; ".join(parse_payload["errors"]),
            "claim_label": "Empirical gap",
            "verified_in_lean": False,
            "verification_method": "not_run",
            "side_conditions": [],
        }
        report_text = """## Proposed architecture

- EML verification could not start because parsing failed.

## Formal evidence from mathlib

- No Lean proof attempt was launched.

## Engineering inference built on top of formal facts

- v1 verification is conservative and only operates on explicit scalar formulas.

## Gaps requiring benchmarks or papers

- Rewrite the formula into supported scalar syntax before retrying.

## Risks

- Guessing a parse would turn an unsupported claim into fake formal support.
"""

    records = replace_record(load_existing_evidence(layout["evidence_json"]), evidence_record)
    layout["evidence_json"].write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    layout["report_md"].write_text(report_text, encoding="utf-8")
    layout["session_log_json"].write_text(
        json.dumps(
            {
                "phase": "eml_verify",
                "formula": formula,
                "parse_status": parse_status,
                "compile_status": compile_status,
                "scratch_target": scratch_target,
                "lean_check": lean_payload,
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
        "verified_in_lean": verified_in_lean,
        "verification_method": verification_method,
        "bootstrap": bootstrap_payload,
        "validation": validation,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(payload)

    return 0 if validation.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
