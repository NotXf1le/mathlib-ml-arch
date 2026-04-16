from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ArtifactTargets:
    report_path: Path | None
    evidence_path: Path | None


@dataclass(frozen=True)
class ArtifactResolutionDependencies:
    plugin_root: Callable[[], Path]
    default_workspace_root: Callable[[], Path]


class ArtifactTargetResolver:
    def __init__(self, dependencies: ArtifactResolutionDependencies) -> None:
        self._deps = dependencies

    def resolve(self, args: object) -> ArtifactTargets:
        bundle_dir = getattr(args, "bundle_dir", None)
        report_arg = getattr(args, "report", None)
        evidence_arg = getattr(args, "evidence", None)
        latest = bool(getattr(args, "latest", False))
        workspace_arg = getattr(args, "workspace", None)

        if bundle_dir:
            resolved_bundle_dir = Path(bundle_dir).expanduser().resolve()
            report = report_in_bundle_dir(resolved_bundle_dir)
            evidence = Path(evidence_arg).expanduser().resolve() if evidence_arg else resolved_bundle_dir / "evidence.json"
            return ArtifactTargets(report_path=report, evidence_path=evidence)

        if report_arg:
            report = Path(report_arg).expanduser().resolve()
            evidence = Path(evidence_arg).expanduser().resolve() if evidence_arg else report.with_name("evidence.json")
            return ArtifactTargets(report_path=report, evidence_path=evidence)

        if latest:
            workspace_root = Path(workspace_arg).expanduser().resolve() if workspace_arg else self._deps.default_workspace_root()
            report = latest_report(candidate_report_dirs(self._deps.plugin_root(), workspace_root))
            if report is None:
                return ArtifactTargets(report_path=None, evidence_path=None)
            evidence = Path(evidence_arg).expanduser().resolve() if evidence_arg else report.with_name("evidence.json")
            return ArtifactTargets(report_path=report, evidence_path=evidence)

        return ArtifactTargets(report_path=None, evidence_path=None)


def candidate_report_dirs(plugin_root: Path, workspace_root: Path) -> list[Path]:
    candidates = [plugin_root / "reports", workspace_root / "reports"]
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


class ArtifactBundleValidator:
    def __init__(
        self,
        required_sections: list[str],
        required_evidence_fields: list[str],
        required_side_condition_fields: list[str],
    ) -> None:
        self._required_sections = required_sections
        self._required_evidence_fields = required_evidence_fields
        self._required_side_condition_fields = required_side_condition_fields

    def heading_positions(self, report_text: str) -> dict[str, int]:
        positions: dict[str, int] = {}
        for section in self._required_sections:
            match = re.search(rf"^\s{{0,3}}##\s+{re.escape(section)}\s*$", report_text, flags=re.MULTILINE)
            positions[section] = match.start() if match else -1
        return positions

    def validate_report(self, report_path: Path) -> tuple[list[str], dict[str, int]]:
        issues: list[str] = []
        if not report_path.exists():
            return [f"Missing report file: {report_path}"], {}

        report_text = report_path.read_text(encoding="utf-8")
        positions = self.heading_positions(report_text)
        missing = [section for section, position in positions.items() if position < 0]
        for section in missing:
            issues.append(f"Missing section: {section}")

        last_position = -1
        for section in self._required_sections:
            position = positions.get(section, -1)
            if position < 0:
                continue
            if position < last_position:
                issues.append(f"Section out of order: {section}")
            last_position = position

        return issues, positions

    def load_evidence_records(self, payload: object) -> list[dict[str, object]]:
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

    def validate_evidence(self, evidence_path: Path) -> tuple[list[str], list[dict[str, object]]]:
        issues: list[str] = []
        if not evidence_path.exists():
            return [f"Missing evidence file: {evidence_path}"], []

        try:
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [f"evidence.json is not valid JSON: {exc.msg}"], []

        records = self.load_evidence_records(payload)
        if not records:
            issues.append("evidence.json should be an array or expose non-empty records/claims.")
            return issues, records

        for index, record in enumerate(records, start=1):
            for field in self._required_evidence_fields:
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
                        issues.append(f"Evidence record {index} side condition {condition_index} must be an object.")
                        continue
                    for field in self._required_side_condition_fields:
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
