from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


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


def default_output_dir() -> Path:
    return plugin_root() / "reports" / "mathlib_ml_arch_demo"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the explicit mathlib-ml-arch demo review flow and emit report.md plus evidence.json."
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run the bundled fixture-based happy path.",
    )
    parser.add_argument(
        "--fixture",
        default="review-architecture",
        help="Fixture name under fixtures/. Only review-architecture is shipped in this release.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory where report.md and evidence.json should be written.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip the final artifact validation step.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable output.",
    )
    return parser.parse_args()


def fixture_dir(name: str) -> Path:
    return plugin_root() / "fixtures" / name


def run_validation(output_dir: Path) -> dict[str, object]:
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
        payload = {
            "valid": False,
            "issues": ["Validator returned unreadable output."],
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    payload["validator_exit_code"] = result.returncode
    return payload


def print_human(payload: dict[str, object]) -> None:
    print(f"fixture: {payload['fixture']}")
    print(f"input: {payload['input_path']}")
    print(f"report: {payload['report_path']}")
    print(f"evidence: {payload['evidence_path']}")
    if payload.get("verified_theorem"):
        print(f"verified theorem: {payload['verified_theorem']}")
    if payload.get("unsupported_boundary"):
        print(f"unsupported boundary: {payload['unsupported_boundary']}")
    if payload.get("validation"):
        validation = payload["validation"]
        print(f"bundle valid: {validation.get('valid', False)}")
        for issue in validation.get("issues", []):
            print(f"  - {issue}")


def main() -> int:
    configure_stdout()
    args = parse_args()
    if not args.demo:
        raise SystemExit("Only the explicit demo flow is shipped in this release. Pass --demo.")

    source_dir = fixture_dir(args.fixture)
    if not source_dir.exists():
        raise SystemExit(f"Fixture not found: {source_dir}")

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    input_path = source_dir / "architecture_notes.md"
    report_source = source_dir / "report.md"
    evidence_source = source_dir / "evidence.json"
    report_target = output_dir / "report.md"
    evidence_target = output_dir / "evidence.json"

    shutil.copyfile(report_source, report_target)
    shutil.copyfile(evidence_source, evidence_target)
    if input_path.exists():
        shutil.copyfile(input_path, output_dir / input_path.name)

    validation = None if args.skip_validate else run_validation(output_dir)
    payload = {
        "fixture": args.fixture,
        "input_path": str(input_path) if input_path.exists() else None,
        "report_path": str(report_target),
        "evidence_path": str(evidence_target),
        "validation": validation,
        "verified_theorem": None,
        "unsupported_boundary": None,
    }

    if validation:
        payload["verified_theorem"] = validation.get("verified_theorem")
        payload["unsupported_boundary"] = validation.get("unsupported_boundary")

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(payload)

    if validation is None:
        return 0
    return 0 if validation.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
