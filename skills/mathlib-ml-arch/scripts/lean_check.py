from __future__ import annotations

import argparse
import os
import shutil
import subprocess
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
        "workspace or one of its parent directories. This plugin does not bundle mathlib sources "
        "by itself. Create `proofs/lean-toolchain`, `proofs/lakefile.toml`, and "
        "`proofs/ProofScratch.lean`, then run `lake update` inside `proofs/` before retrying."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Typecheck a Lean scratch file inside the local proofs project."
    )
    parser.add_argument(
        "--file",
        default="ProofScratch.lean",
        help="Lean file inside proofs/ to typecheck.",
    )
    return parser.parse_args()


def find_lake() -> Path | None:
    path_hit = shutil.which("lake")
    if path_hit is not None:
        return Path(path_hit)

    user_profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    candidates = [
        user_profile / ".elan" / "bin" / "lake.exe",
        user_profile
        / "AppData"
        / "Local"
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Lean.Lean_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "lean-4.29.0-windows"
        / "bin"
        / "lake.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def subprocess_env(lake: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{lake.parent}{os.pathsep}{env.get('PATH', '')}"
    user_profile = env.get("USERPROFILE")
    if user_profile and ".elan" in str(lake):
        env["ELAN_HOME"] = str(Path(user_profile) / ".elan")
        env["HOME"] = user_profile
        env["USERPROFILE"] = user_profile
    return env


def main() -> int:
    args = parse_args()
    try:
        root = repo_root()
    except FileNotFoundError:
        print(missing_proofs_message(), file=sys.stderr)
        return 4

    proofs_dir = root / "proofs"
    target = proofs_dir / args.file

    if not target.exists():
        print(f"Lean target not found: {target}", file=sys.stderr)
        return 1

    lake = find_lake()
    if lake is None:
        print(
            "The `lake` executable was not found on PATH or in standard user install locations. "
            "Install Lean 4, then run `lake update` inside proofs/ before retrying.",
            file=sys.stderr,
        )
        return 2

    mathlib_dir = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
    if not mathlib_dir.exists():
        print(
            "The local mathlib checkout was not found at proofs/.lake/packages/mathlib/Mathlib. "
            "Run `lake update` inside proofs/ after Lean is installed.",
            file=sys.stderr,
        )
        return 3

    result = subprocess.run(
        [str(lake), "env", "lean", target.name],
        cwd=proofs_dir,
        check=False,
        env=subprocess_env(lake),
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
