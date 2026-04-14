from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def repo_root() -> Path:
    search_roots = [Path.cwd(), *Path.cwd().parents, Path(__file__).resolve().parent]
    for root in search_roots:
        if (root / "proofs").is_dir():
            return root
    raise FileNotFoundError(
        "Could not locate the repo root. Run this command from the target repo or a child directory."
    )


def missing_proofs_message() -> str:
    return (
        "No local Lean proofs project was found. Expected a `proofs/` directory in the current "
        "workspace or one of its parent directories. This plugin only searches the workspace's "
        "Lean project; it does not ship a standalone mathlib checkout."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search local proofs and any downloaded mathlib checkout for a query."
    )
    parser.add_argument("query", help="Literal text to search for.")
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        help="Search case-insensitively.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="Maximum number of matches to print.",
    )
    return parser.parse_args()


def candidate_dirs(root: Path) -> list[Path]:
    proofs_dir = root / "proofs"
    return [
        proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib",
        proofs_dir,
    ]


def iter_lean_files(directories: list[Path]) -> list[Path]:
    files: list[Path] = []
    for directory in directories:
        if not directory.exists():
            continue
        files.extend(path for path in directory.rglob("*.lean") if path.is_file())
    return files


def search_file(path: Path, pattern: re.Pattern[str]) -> list[str]:
    matches: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if pattern.search(line):
                    matches.append(f"{path}:{line_number}: {line.rstrip()}")
    except UnicodeDecodeError:
        return []
    return matches


def emit_line(line: str) -> None:
    try:
        print(line)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or os.device_encoding(1) or "utf-8"
        safe = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe)


def main() -> int:
    args = parse_args()
    try:
        root = repo_root()
    except FileNotFoundError:
        print(missing_proofs_message(), file=sys.stderr)
        return 2

    directories = candidate_dirs(root)
    available_dirs = [directory for directory in directories if directory.exists()]

    if not available_dirs:
        print(
            "No searchable Lean sources were found. Expected at least the proofs/ directory.",
            file=sys.stderr,
        )
        return 1

    flags = re.IGNORECASE if args.ignore_case else 0
    pattern = re.compile(re.escape(args.query), flags)
    files = iter_lean_files(available_dirs)
    if not files:
        print("No .lean files were found in the searchable directories.", file=sys.stderr)
        return 1

    emitted = 0
    for path in files:
        for match in search_file(path, pattern):
            emit_line(match)
            emitted += 1
            if emitted >= args.max_results:
                return 0

    if emitted == 0:
        print(f"No matches found for {args.query!r}.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
