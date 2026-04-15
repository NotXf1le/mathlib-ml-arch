from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from common import (
    add_git_safe_directories,
    build_lean_path,
    configure_stdout,
    discover_package_lib_dirs,
    git_safe_directories_for_proofs,
    find_lake,
    find_lean,
    requested_workspace_root,
    resolve_proofs_workspace,
    shared_workspace_root,
    subprocess_env_for_tool,
)


def missing_proofs_message(scope: str) -> str:
    shared_root = shared_workspace_root()
    return (
        "No shared Lean proofs project was found. Expected a `proofs/` directory under "
        f"{shared_root}. Run bootstrap_proofs.py first before retrying."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Typecheck a Lean scratch file inside the shared proofs project."
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root or child directory to search from. Defaults to the current directory.",
    )
    parser.add_argument(
        "--scope",
        choices=["auto", "local", "shared"],
        default="auto",
        help="Which proofs workspace to verify. The plugin is shared-workspace-only; `auto`, `shared`, and legacy `local` all resolve to the shared CODEX_HOME cache.",
    )
    parser.add_argument(
        "--file",
        default="ProofScratch.lean",
        help="Lean file inside proofs/ to typecheck.",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "lake", "direct"],
        default="auto",
        help="Verification mode. `auto` tries `lake env lean` first, then falls back to direct lean.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="Per-command timeout for `lake env lean` and direct `lean` invocations.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON diagnostics.",
    )
    return parser.parse_args()


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, object]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        return {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.returncode == 0,
            "timed_out": False,
            "timeout_seconds": timeout_seconds,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": None,
            "stdout": _coerce_text(exc.stdout),
            "stderr": (
                f"{_coerce_text(exc.stderr).rstrip()}\nCommand timed out after {timeout_seconds} seconds."
            ).strip(),
            "success": False,
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
        }


def print_human_summary(payload: dict[str, object]) -> None:
    if payload["success"]:
        method = payload.get("verification_method", "unknown")
        print(f"Verified {payload['target']} via {method}.")
    else:
        print(f"Verification failed for {payload['target']}.", file=sys.stderr)

    print(f"requested workspace: {payload['requested_workspace']}")
    print(f"selected workspace: {payload['workspace_root']} ({payload['selected_scope'] or 'none'})")
    print(f"proofs: {payload['proofs_dir']}")
    print(f"lake: {payload.get('lake_path') or 'not found'}")
    print(f"lean: {payload.get('lean_path') or 'not found'}")
    print(f"library paths: {payload['library_path_count']}")

    for method in payload["methods"]:
        label = method["name"]
        outcome = "ok" if method["success"] else f"failed ({method['returncode']})"
        print(f"{label}: {outcome}")
        stderr = str(method.get("stderr", "")).strip()
        if stderr:
            print(f"  stderr: {stderr.splitlines()[-1]}")


def main() -> int:
    configure_stdout()
    args = parse_args()
    requested_workspace = requested_workspace_root(args.workspace)
    root, selected_scope = resolve_proofs_workspace(requested_workspace, args.scope)
    if root is None:
        payload = {
            "success": False,
            "error": missing_proofs_message(args.scope),
            "requested_workspace": str(requested_workspace),
            "workspace_root": str(requested_workspace),
            "selected_scope": None,
            "shared_workspace_root": str(shared_workspace_root()),
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(missing_proofs_message(args.scope), file=sys.stderr)
        return 4

    proofs_dir = root / "proofs"
    target = Path(args.file)
    if not target.is_absolute():
        target = proofs_dir / target
    target = target.resolve()

    if not target.exists():
        payload = {
            "success": False,
            "error": f"Lean target not found: {target}",
            "requested_workspace": str(requested_workspace),
            "workspace_root": str(root),
            "selected_scope": selected_scope,
            "proofs_dir": str(proofs_dir),
            "target": str(target),
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"Lean target not found: {target}", file=sys.stderr)
        return 1

    lake = find_lake()
    lean = find_lean(lake)
    lib_dirs = discover_package_lib_dirs(proofs_dir)
    relative_target = str(target.relative_to(proofs_dir))

    payload: dict[str, object] = {
        "success": False,
        "requested_workspace": str(requested_workspace),
        "workspace_root": str(root),
        "selected_scope": selected_scope,
        "proofs_dir": str(proofs_dir),
        "target": str(target),
        "lake_path": str(lake) if lake else None,
        "lean_path": str(lean) if lean else None,
        "library_paths": [str(path) for path in lib_dirs],
        "library_path_count": len(lib_dirs),
        "methods": [],
        "verification_method": None,
    }

    if args.mode in {"auto", "lake"} and lake is not None:
        lake_env = subprocess_env_for_tool(lake)
        add_git_safe_directories(lake_env, git_safe_directories_for_proofs(proofs_dir))
        record = run_command(
            [str(lake), "env", "lean", relative_target],
            cwd=proofs_dir,
            env=lake_env,
            timeout_seconds=args.timeout_seconds,
        )
        record["name"] = "lake env lean"
        payload["methods"].append(record)
        if record["success"]:
            payload["success"] = True
            payload["verification_method"] = "lake env lean"

    can_try_direct = args.mode in {"auto", "direct"} and lean is not None and len(lib_dirs) > 0
    if not payload["success"] and can_try_direct:
        env = subprocess_env_for_tool(lean)
        discovered_path = build_lean_path(proofs_dir)
        existing_path = env.get("LEAN_PATH", "")
        env["LEAN_PATH"] = (
            f"{discovered_path}{os.pathsep}{existing_path}" if existing_path else discovered_path
        )
        record = run_command(
            [str(lean), relative_target],
            cwd=proofs_dir,
            env=env,
            timeout_seconds=args.timeout_seconds,
        )
        record["name"] = "direct lean with LEAN_PATH"
        payload["methods"].append(record)
        if record["success"]:
            payload["success"] = True
            payload["verification_method"] = "direct lean with LEAN_PATH fallback"

    if not payload["success"] and not payload["methods"]:
        missing = []
        if args.mode in {"auto", "lake"} and lake is None:
            missing.append("lake executable not found")
        if args.mode in {"auto", "direct"}:
            if lean is None:
                missing.append("lean executable not found")
            if not lib_dirs:
                missing.append("no compiled package libraries were found under proofs/.lake")
        payload["error"] = "; ".join(missing) or "No verification method could be attempted."

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human_summary(payload)

    return 0 if payload["success"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
