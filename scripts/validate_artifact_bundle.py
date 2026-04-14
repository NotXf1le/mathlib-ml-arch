from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


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
    candidates = [plugin_root() / "reports", workspace_root / "reports"]
    return [path.resolve() for path in candidates if path.exists()]


def latest_report(candidate_dirs: list[Path]) -> Path | None:
    reports: list[Path] = []
    for directory in candidate_dirs:
        reports.extend(
            path
            for path in directory.rglob("*")
            if path.is_file() and (path.name == "report.md" or re.fullmatch(r"architecture_audit_report.*\.md", path.name))
        )
    if not reports:
        return None
    return max(reports, key=lambda path: path.stat().st_mtime)


def report_in_bundle_dir(bundle_dir: Path) -> Path | None:
    if not bundle_dir.exists():
        return None

    report = bundle_dir / "report.md"
    if report.exists():
        return report

    candidates = [
        path
        for path in bundle_dir.iterdir()
        if path.is_file() and re.fullmatch(r"architecture_audit_report.*\.md", path.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def resolve_targets(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    if args.bundle_dir:
        bundle_dir = Path(args.bundle_dir).expanduser().resolve()
        report = report_in_bundle_dir(bundle_dir)
        evidence = Path(args.evidence).expanduser().resolve() if args.evidence else bundle_dir / "evidence.json"
        return report, evidence

    if args.report:
        report = Path(args.report).expanduser().resolve()
        evidence = Path(args.evidence).expanduser().resolve() if args.evidence else report.with_name("evidence.json")
        return report, evidence

    if args.latest:
        workspace_root = Path(args.workspace).expanduser().resolve() if args.workspace else default_workspace_root()
        report = latest_report(candidate_report_dirs(workspace_root))
        if report is None:
            return None, None
        evidence = Path(args.evidence).expanduser().resolve() if args.evidence else report.with_name("evidence.json")
        return report, evidence

    return None, None


def heading_positions(report_text: str) -> dict[str, int]:
    positions: dict[str, int] = {}
    for section in REQUIRED_SECTIONS:
        match = re.search(rf"^\s{{0,3}}##\s+{re.escape(section)}\s*$", report_text, flags=re.MULTILINE)
        positions[section] = match.start() if match else -1
    return positions


def validate_report(report_path: Path) -> tuple[list[str], dict[str, int]]:
    issues: list[str] = []
    if not report_path.exists():
        return [f"Missing report file: {report_path}"], {}

    report_text = report_path.read_text(encoding="utf-8")
    positions = heading_positions(report_text)

    missing = [section for section, position in positions.items() if position < 0]
    for section in missing:
        issues.append(f"Missing section: {section}")

    last_position = -1
    for section in REQUIRED_SECTIONS:
        position = positions.get(section, -1)
        if position < 0:
            continue
        if position < last_position:
            issues.append(f"Section out of order: {section}")
        last_position = position

    return issues, positions


def load_evidence_records(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
        claims = payload.get("claims")
        if isinstance(claims, list):
            return [item for item in claims if isinstance(item, dict)]
    return []


def validate_evidence(evidence_path: Path) -> tuple[list[str], list[dict[str, object]]]:
    issues: list[str] = []
    if not evidence_path.exists():
        return [f"Missing evidence file: {evidence_path}"], []

    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"evidence.json is not valid JSON: {exc.msg}"], []

    records = load_evidence_records(payload)
    if not records:
        issues.append("evidence.json should be an array or expose non-empty records/claims.")
        return issues, records

    for index, record in enumerate(records, start=1):
        for field in REQUIRED_EVIDENCE_FIELDS:
            value = record.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                issues.append(f"Evidence record {index} is missing '{field}'.")

        verified = record.get("verified_in_lean")
        if not isinstance(verified, bool):
            issues.append(f"Evidence record {index} must set 'verified_in_lean' to true or false.")

        verification_method = record.get("verification_method")
        if not isinstance(verification_method, str) or not verification_method.strip():
            issues.append(f"Evidence record {index} must set a non-empty 'verification_method'.")

        side_conditions = record.get("side_conditions")
        if not isinstance(side_conditions, list):
            issues.append(f"Evidence record {index} must provide 'side_conditions' as an array.")
        else:
            for condition_index, condition in enumerate(side_conditions, start=1):
                if not isinstance(condition, dict):
                    issues.append(
                        f"Evidence record {index} side condition {condition_index} must be an object."
                    )
                    continue
                for field in REQUIRED_SIDE_CONDITION_FIELDS:
                    value = condition.get(field)
                    if value is None or (isinstance(value, str) and not value.strip()):
                        issues.append(
                            f"Evidence record {index} side condition {condition_index} is missing '{field}'."
                        )

        claim_label = str(record.get("claim_label", "")).casefold()
        if claim_label == "formal support" and verified is not True:
            issues.append(
                f"Evidence record {index} cannot use 'Formal support' when 'verified_in_lean' is false."
            )

    return issues, records


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
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print_human(payload)
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

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(payload)

    return 0 if payload["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
