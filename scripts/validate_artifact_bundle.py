from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from artifact_bundle_service import (
    ArtifactBundleValidator,
    ArtifactResolutionDependencies,
    ArtifactTargetResolver,
    candidate_report_dirs as service_candidate_report_dirs,
    latest_report as service_latest_report,
    report_in_bundle_dir as service_report_in_bundle_dir,
)
from script_output import PayloadEmitter


REQUIRED_SECTIONS = [
    "Proposed architecture",
    "Formal evidence from mathlib",
    "Engineering inference built on top of formal facts",
    "Gaps requiring benchmarks or papers",
    "Risks",
]

REQUIRED_EVIDENCE_FIELDS = [
    "name",
    "import_path",
    "plain_language_meaning",
    "supported_subclaim",
    "unsupported_boundary",
    "claim_label",
    "verified_in_lean",
    "verification_method",
    "side_conditions",
]

REQUIRED_SIDE_CONDITION_FIELDS = [
    "kind",
    "condition",
    "status",
]


def configure_stdout() -> None:
    stream = getattr(sys, "stdout", None)
    if stream is None or not hasattr(stream, "reconfigure"):
        return
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except ValueError:
        pass


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_workspace_root() -> Path:
    return Path.cwd().resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a mathlib-ml-arch artifact bundle without relying on hooks."
    )
    parser.add_argument(
        "--bundle-dir",
        help="Directory containing report.md and evidence.json.",
    )
    parser.add_argument(
        "--report",
        help="Path to a report file. evidence.json is resolved next to it unless --evidence is passed.",
    )
    parser.add_argument(
        "--evidence",
        help="Path to evidence.json. Optional when --report or --bundle-dir is used.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Inspect the most recent bundle under plugin-root/reports or workspace-root/reports.",
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root used when --latest is selected. Defaults to the parent workspace of this plugin.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable output.",
    )
    return parser.parse_args()


def candidate_report_dirs(workspace_root: Path) -> list[Path]:
    return service_candidate_report_dirs(plugin_root(), workspace_root)


def latest_report(candidate_dirs: list[Path]) -> Path | None:
    return service_latest_report(candidate_dirs)


def report_in_bundle_dir(bundle_dir: Path) -> Path | None:
    return service_report_in_bundle_dir(bundle_dir)


def resolve_targets(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    targets = ArtifactTargetResolver(
        ArtifactResolutionDependencies(
            plugin_root=plugin_root,
            default_workspace_root=default_workspace_root,
        )
    ).resolve(args)
    return targets.report_path, targets.evidence_path


def heading_positions(report_text: str) -> dict[str, int]:
    return _validator().heading_positions(report_text)


def validate_report(report_path: Path) -> tuple[list[str], dict[str, int]]:
    return _validator().validate_report(report_path)


def load_evidence_records(payload: object) -> list[dict[str, object]]:
    return _validator().load_evidence_records(payload)


def validate_evidence(evidence_path: Path) -> tuple[list[str], list[dict[str, object]]]:
    return _validator().validate_evidence(evidence_path)


def _validator() -> ArtifactBundleValidator:
    return ArtifactBundleValidator(
        required_sections=REQUIRED_SECTIONS,
        required_evidence_fields=REQUIRED_EVIDENCE_FIELDS,
        required_side_condition_fields=REQUIRED_SIDE_CONDITION_FIELDS,
    )


def summary_from_records(records: list[dict[str, object]]) -> dict[str, object]:
    verified = next(
        (
            record
            for record in records
            if bool(record.get("verified_in_lean"))
        ),
        None,
    )
    unsupported = next(
        (record for record in records if record.get("unsupported_boundary")),
        None,
    )
    return {
        "verified_theorem": verified.get("name") if verified else None,
        "unsupported_boundary": unsupported.get("unsupported_boundary") if unsupported else None,
        "record_count": len(records),
    }


def print_human(payload: dict[str, object]) -> None:
    status = "valid" if payload["valid"] else "invalid"
    print(f"bundle: {status}")
    if payload.get("report_path"):
        print(f"report: {payload['report_path']}")
    if payload.get("evidence_path"):
        print(f"evidence: {payload['evidence_path']}")
    if payload.get("verified_theorem"):
        print(f"verified theorem: {payload['verified_theorem']}")
    if payload.get("unsupported_boundary"):
        print(f"unsupported boundary: {payload['unsupported_boundary']}")
    if payload["issues"]:
        print("issues:")
        for issue in payload["issues"]:
            print(f"  - {issue}")


def main() -> int:
    configure_stdout()
    args = parse_args()
    report_path, evidence_path = resolve_targets(args)

    if report_path is None or evidence_path is None:
        payload = {
            "valid": False,
            "report_path": None,
            "evidence_path": None,
            "issues": ["No artifact bundle could be located."],
        }
        PayloadEmitter(json_enabled=args.json, human_printer=print_human).emit(payload)
        return 2

    report_issues, _ = validate_report(report_path)
    evidence_issues, records = validate_evidence(evidence_path)
    summary = summary_from_records(records)
    issues = [*report_issues, *evidence_issues]

    payload = {
        "valid": not issues,
        "report_path": str(report_path),
        "evidence_path": str(evidence_path),
        "issues": issues,
        **summary,
    }

    PayloadEmitter(json_enabled=args.json, human_printer=print_human).emit(payload)

    return 0 if payload["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
