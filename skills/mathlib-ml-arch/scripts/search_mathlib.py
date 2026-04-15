from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from common import configure_stdout, requested_workspace_root, resolve_proofs_workspace, shared_workspace_root


DECLARATION_RE = re.compile(
    r"^\s*(?:(?:private|protected|noncomputable|unsafe|partial|opaque|scoped|local)\s+)*"
    r"(?P<kind>theorem|lemma|def|abbrev|axiom|class|structure|inductive)\s+"
    r"(?P<name>[A-Za-z0-9_'.]+)"
)
NAMESPACE_RE = re.compile(r"^\s*namespace\s+(?P<name>[A-Za-z0-9_'.]+)\s*$")
END_RE = re.compile(r"^\s*end\b")


def missing_proofs_message(scope: str) -> str:
    shared_root = shared_workspace_root()
    return (
        "No shared Lean proofs project was found. Expected a `proofs/` directory under "
        f"{shared_root}. Run bootstrap_proofs.py first."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search the shared proofs workspace and mathlib for theorem-aware candidate matches."
    )
    parser.add_argument("query", help="Literal text to search for.")
    parser.add_argument(
        "--workspace",
        help="Workspace root or child directory to search from. Defaults to the current directory.",
    )
    parser.add_argument(
        "--scope",
        choices=["auto", "local", "shared"],
        default="auto",
        help="Which proofs workspace to search. The plugin is shared-workspace-only; `auto`, `shared`, and legacy `local` all resolve to the shared CODEX_HOME cache.",
    )
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        help="Search case-insensitively.",
    )
    parser.add_argument(
        "--mode",
        choices=["candidates", "raw", "both"],
        default="candidates",
        help="Emit declaration candidates, raw line hits, or both.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
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
    return sorted(set(files))


def query_tokens(query: str) -> list[str]:
    tokens = [token for token in re.split(r"[^A-Za-z0-9_'.]+", query) if token]
    return tokens or [query]


def prefilter_terms(query: str) -> list[str]:
    terms = [query]
    if "." in query:
        parts = [part for part in query.split(".") if part]
        if parts:
            terms.append(parts[-1])
            if len(parts) > 1:
                terms.append(parts[0])
    return list(dict.fromkeys(terms))


def rg_prefilter(
    directories: list[Path],
    query: str,
    ignore_case: bool,
    limit: int,
) -> list[Path] | None:
    rg = shutil.which("rg")
    if rg is None:
        return None

    hits: list[Path] = []
    seen: set[Path] = set()
    base_command = [rg, "--files-with-matches", "--glob", "*.lean", "-F"]
    if ignore_case:
        base_command.append("-i")

    for term in prefilter_terms(query):
        command = [*base_command, term, *[str(directory) for directory in directories]]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode not in {0, 1}:
            return None
        for line in result.stdout.splitlines():
            path = Path(line).resolve()
            if path not in seen:
                hits.append(path)
                seen.add(path)
                if len(hits) >= limit:
                    return hits

    return hits


def import_path_for(path: Path, source_root: Path, prefix: str = "") -> str:
    relative = path.relative_to(source_root)
    import_path = ".".join(relative.with_suffix("").parts)
    return f"{prefix}{import_path}" if prefix else import_path


def full_name(namespace_stack: list[str], name: str) -> str:
    prefix = ".".join(namespace_stack)
    return f"{prefix}.{name}" if prefix else name


def candidate_score(
    query: str,
    tokens: list[str],
    declaration_name: str,
    import_path: str,
    line: str,
    kind: str,
    ignore_case: bool,
) -> int:
    normalize = str.casefold if ignore_case else (lambda value: value)
    query_key = normalize(query)
    name_key = normalize(declaration_name)
    import_key = normalize(import_path)
    line_key = normalize(line)

    score = 0
    matched = False
    if name_key == query_key:
        score += 120
        matched = True
    elif normalize(declaration_name.split(".")[-1]) == query_key:
        score += 100
        matched = True
    elif query_key in name_key:
        score += 80
        matched = True

    if query_key in line_key:
        score += 30
        matched = True
    if query_key in import_key:
        score += 15
        matched = True

    for token in tokens:
        token_key = normalize(token)
        if token_key in name_key:
            score += 18
            matched = True
        if token_key in import_key:
            score += 4
            matched = True
        if token_key in line_key:
            score += 6
            matched = True

    if matched and kind in {"theorem", "lemma"}:
        score += 5

    return score


def scan_file(
    path: Path,
    pattern: re.Pattern[str],
    query: str,
    tokens: list[str],
    mathlib_root: Path | None,
    proofs_root: Path,
    ignore_case: bool,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    declaration_hits: list[dict[str, object]] = []
    raw_hits: list[dict[str, object]] = []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return declaration_hits, raw_hits

    namespace_stack: list[str] = []
    in_mathlib = mathlib_root is not None and mathlib_root in path.parents
    source_root = mathlib_root if in_mathlib else proofs_root
    import_path = import_path_for(path, source_root, prefix="Mathlib." if in_mathlib else "")

    for line_number, line in enumerate(lines, start=1):
        namespace_match = NAMESPACE_RE.match(line)
        if namespace_match:
            namespace_stack.append(namespace_match.group("name"))
            continue

        if END_RE.match(line) and namespace_stack:
            namespace_stack.pop()
            continue

        if pattern.search(line):
            raw_hits.append(
                {
                    "path": str(path),
                    "line_number": line_number,
                    "line": line.rstrip(),
                }
            )

        declaration_match = DECLARATION_RE.match(line)
        if not declaration_match:
            continue

        name = declaration_match.group("name")
        kind = declaration_match.group("kind")
        qualified_name = full_name(namespace_stack, name)
        score = candidate_score(query, tokens, qualified_name, import_path, line, kind, ignore_case)
        if score <= 0:
            continue

        declaration_hits.append(
            {
                "score": score,
                "kind": kind,
                "name": qualified_name,
                "short_name": name,
                "import_path": import_path,
                "path": str(path),
                "line_number": line_number,
                "line": line.rstrip(),
            }
        )

    return declaration_hits, raw_hits


def print_text_results(candidates: list[dict[str, object]], raw_hits: list[dict[str, object]], mode: str) -> None:
    if mode in {"candidates", "both"}:
        if candidates:
            for candidate in candidates:
                print(
                    f"[score={candidate['score']}] {candidate['kind']} {candidate['name']} "
                    f"({candidate['import_path']})"
                )
                print(f"  {candidate['path']}:{candidate['line_number']}")
                print(f"  {candidate['line']}")
        elif mode == "candidates":
            print("No declaration candidates found.", file=sys.stderr)

    if mode == "both" and candidates and raw_hits:
        print()

    if mode in {"raw", "both"}:
        if raw_hits:
            for raw_hit in raw_hits:
                print(f"{raw_hit['path']}:{raw_hit['line_number']}: {raw_hit['line']}")
        elif mode == "raw":
            print("No raw text matches found.", file=sys.stderr)


def main() -> int:
    configure_stdout()
    args = parse_args()
    requested_workspace = requested_workspace_root(args.workspace)
    root, selected_scope = resolve_proofs_workspace(requested_workspace, args.scope)
    if root is None:
        print(missing_proofs_message(args.scope), file=sys.stderr)
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
    files = rg_prefilter(available_dirs, args.query, args.ignore_case, max(args.max_results * 10, 50))
    if not files:
        files = iter_lean_files(available_dirs)
    if not files:
        print("No .lean files were found in the searchable directories.", file=sys.stderr)
        return 1

    tokens = query_tokens(args.query)
    proofs_root = root / "proofs"
    mathlib_root = directories[0] if directories[0].exists() else None
    candidates: list[dict[str, object]] = []
    raw_hits: list[dict[str, object]] = []

    for path in files:
        declaration_hits, file_raw_hits = scan_file(
            path,
            pattern,
            args.query,
            tokens,
            mathlib_root,
            proofs_root,
            args.ignore_case,
        )
        candidates.extend(declaration_hits)
        raw_hits.extend(file_raw_hits)

    candidates.sort(
        key=lambda item: (
            -int(item["score"]),
            str(item["name"]).casefold(),
            int(item["line_number"]),
        )
    )
    raw_hits.sort(key=lambda item: (str(item["path"]).casefold(), int(item["line_number"])))

    candidates = candidates[: args.max_results]
    raw_hits = raw_hits[: args.max_results]

    if args.json:
        payload = {
            "requested_workspace": str(requested_workspace),
            "workspace_root": str(root),
            "selected_scope": selected_scope,
            "query": args.query,
            "mode": args.mode,
            "candidates": candidates if args.mode in {"candidates", "both"} else [],
            "raw_matches": raw_hits if args.mode in {"raw", "both"} else [],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["candidates"] or payload["raw_matches"] else 1

    print_text_results(candidates, raw_hits, args.mode)

    if not candidates and not raw_hits:
        print(f"No matches found for {args.query!r}.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
