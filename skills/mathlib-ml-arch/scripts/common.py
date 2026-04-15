from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


WINDOWS = os.name == "nt"
PLUGIN_SLUG = "mathlib-ml-arch"
READINESS_SMOKE_TIMEOUT_SECONDS = 120
READINESS_SMOKE_FILENAME = "__CodexVerificationReadiness.lean"
READINESS_SMOKE_SOURCE = """import Mathlib

#check True.intro
"""


def configure_stdout() -> None:
    stream = getattr(sys, "stdout", None)
    if stream is None or not hasattr(stream, "reconfigure"):
        return
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except ValueError:
        pass


def safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()
    except RuntimeError:
        return path.absolute()


def normalize_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    return safe_resolve(Path(path).expanduser())


def path_contains(path: Path, root: Path) -> bool:
    try:
        safe_resolve(path).relative_to(safe_resolve(root))
        return True
    except ValueError:
        return False


def requested_workspace_root(start: str | Path | None = None) -> Path:
    return normalize_path(start) or Path.cwd().resolve()


def find_existing_proofs_root(start: str | Path | None = None) -> Path | None:
    anchor = requested_workspace_root(start)
    for root in [anchor, *anchor.parents]:
        if (root / "proofs").is_dir():
            return root
    return None


def infer_workspace_root(start: str | Path | None = None) -> Path:
    return requested_workspace_root(start)


def existing_parent_for_probe(path: Path) -> Path:
    candidate = safe_resolve(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def writability_error(path: str | Path | None) -> str | None:
    normalized = normalize_path(path)
    if normalized is None:
        return "No path was provided."

    probe_root = existing_parent_for_probe(normalized)
    if not probe_root.exists():
        return f"No existing parent directory is available for {normalized}."

    target_root = normalized if normalized.exists() and normalized.is_dir() else probe_root
    probe_dir = target_root / f".codex-write-probe-{os.getpid()}-{time.time_ns()}"
    try:
        probe_dir.mkdir()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"

    try:
        probe_dir.rmdir()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"

    return None


def is_writable_path(path: str | Path | None) -> bool:
    return writability_error(path) is None


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return safe_resolve(Path(configured).expanduser())

    profile = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    base = safe_resolve(Path(profile).expanduser()) if profile else safe_resolve(Path.home())
    return base / ".codex"


def shared_workspace_roots(plugin_slug: str = PLUGIN_SLUG) -> list[Path]:
    roots = [safe_resolve(codex_home() / "cache" / plugin_slug / "shared_workspace")]
    temp_root = safe_resolve(Path(tempfile.gettempdir()) / "codex" / plugin_slug / "shared_workspace")
    if temp_root not in roots:
        roots.append(temp_root)
    return roots


def shared_workspace_root(plugin_slug: str = PLUGIN_SLUG) -> Path:
    candidates = shared_workspace_roots(plugin_slug)
    for candidate in candidates:
        if (candidate / "proofs").is_dir():
            return candidate
    for candidate in candidates:
        if writability_error(candidate) is None:
            return candidate
    return candidates[0]


def toolchain_fallback_roots(plugin_slug: str = PLUGIN_SLUG) -> list[Path]:
    roots = [safe_resolve(codex_home() / "cache" / plugin_slug / "toolchains")]
    temp_root = safe_resolve(Path(tempfile.gettempdir()) / "codex" / plugin_slug / "toolchains")
    if temp_root not in roots:
        roots.append(temp_root)
    return roots


def cached_elan_homes(plugin_slug: str = PLUGIN_SLUG) -> list[Path]:
    return [safe_resolve(root / "elan") for root in toolchain_fallback_roots(plugin_slug)]


def fallback_tool_home(plugin_slug: str = PLUGIN_SLUG) -> Path:
    return safe_resolve(toolchain_fallback_roots(plugin_slug)[0] / "home")


def fallback_elan_home(plugin_slug: str = PLUGIN_SLUG) -> Path:
    return safe_resolve(toolchain_fallback_roots(plugin_slug)[0] / "elan")


def prepare_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return writability_error(path) is None


def resolve_fallback_tool_homes(
    configured_elan_home: str | Path | None = None,
    plugin_slug: str = PLUGIN_SLUG,
) -> tuple[Path, Path]:
    configured = normalize_path(configured_elan_home)
    last_home = fallback_tool_home(plugin_slug)
    last_elan = configured or fallback_elan_home(plugin_slug)

    for root in toolchain_fallback_roots(plugin_slug):
        home_candidate = safe_resolve(root / "home")
        elan_candidate = configured or safe_resolve(root / "elan")
        if not prepare_writable_directory(home_candidate):
            last_home, last_elan = home_candidate, elan_candidate
            continue
        if configured is not None:
            if not prepare_writable_directory(configured):
                last_home, last_elan = home_candidate, configured
                continue
            return home_candidate, configured
        if not prepare_writable_directory(elan_candidate):
            last_home, last_elan = home_candidate, elan_candidate
            continue
        return home_candidate, elan_candidate

    return last_home, last_elan


def executable_names(tool_name: str) -> list[str]:
    if WINDOWS:
        primary = tool_name if tool_name.endswith(".exe") else f"{tool_name}.exe"
        secondary = tool_name[:-4] if tool_name.endswith(".exe") else tool_name
        return [primary] if primary == secondary else [primary, secondary]
    return [tool_name[:-4] if tool_name.endswith(".exe") else tool_name]


def toolchain_root_from_binary(path: Path | None) -> Path | None:
    if path is None:
        return None
    normalized = safe_resolve(path)
    if normalized.parent.name == "bin":
        return normalized.parent.parent
    return None


def toolchain_is_complete(root: Path | None) -> bool:
    if root is None:
        return False
    lean_lib = root / "lib" / "lean"
    required = [lean_lib / "Std.olean", lean_lib / "Lake.olean", lean_lib / "Lean.olean"]
    return all(path.exists() for path in required)


def iter_cached_tool_candidates(tool_name: str, plugin_slug: str = PLUGIN_SLUG) -> list[Path]:
    names = executable_names(tool_name)
    candidates: list[Path] = []
    seen: set[Path] = set()

    if tool_name == "elan":
        for elan_home in cached_elan_homes(plugin_slug):
            for name in names:
                proxy = safe_resolve(elan_home / "bin" / name)
                if proxy.exists() and proxy not in seen:
                    candidates.append(proxy)
                    seen.add(proxy)

    for elan_home in cached_elan_homes(plugin_slug):
        toolchains_dir = elan_home / "toolchains"
        if not toolchains_dir.exists():
            continue

        for toolchain_dir in sorted(path for path in toolchains_dir.iterdir() if path.is_dir()):
            if not toolchain_is_complete(toolchain_dir):
                continue
            for name in names:
                candidate = safe_resolve(toolchain_dir / "bin" / name)
                if candidate.exists() and candidate not in seen:
                    candidates.append(candidate)
                    seen.add(candidate)

    return candidates


def find_cached_tool(tool_name: str, plugin_slug: str = PLUGIN_SLUG) -> Path | None:
    candidates = iter_cached_tool_candidates(tool_name, plugin_slug)
    return candidates[0] if candidates else None


def find_shared_proofs_root(plugin_slug: str = PLUGIN_SLUG) -> Path | None:
    for root in shared_workspace_roots(plugin_slug):
        if (root / "proofs").is_dir():
            return root
    return None


def is_shared_workspace(root: str | Path | None, plugin_slug: str = PLUGIN_SLUG) -> bool:
    normalized = normalize_path(root)
    if normalized is None:
        return False
    return normalized in shared_workspace_roots(plugin_slug)


def resolve_proofs_workspace(
    start: str | Path | None = None,
    scope: str = "auto",
    plugin_slug: str = PLUGIN_SLUG,
) -> tuple[Path | None, str | None]:
    shared_root = find_shared_proofs_root(plugin_slug)
    if shared_root is not None:
        return shared_root, "shared"
    return None, None


def derive_user_profile_from_tool(tool: Path | None) -> Path | None:
    if tool is None:
        return None

    normalized = safe_resolve(tool)
    for root in toolchain_fallback_roots():
        elan_root = safe_resolve(root / "elan")
        if path_contains(normalized, elan_root):
            return safe_resolve(root / "home")

    parent = tool.parent
    while parent != parent.parent:
        if parent.name.lower() == ".elan":
            return parent.parent
        parent = parent.parent

    user_profile = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    return safe_resolve(Path(user_profile).expanduser()) if user_profile else None


def derive_elan_home_from_tool(tool: Path | None) -> Path | None:
    if tool is None:
        return None

    normalized = safe_resolve(tool)
    for elan_root in cached_elan_homes():
        if path_contains(normalized, elan_root):
            return elan_root

    parent = normalized.parent
    while parent != parent.parent:
        if parent.name.lower() == ".elan":
            return parent
        parent = parent.parent

    configured = os.environ.get("ELAN_HOME")
    if configured:
        return safe_resolve(Path(configured).expanduser())

    profile = derive_user_profile_from_tool(tool)
    if profile is None:
        return None
    return safe_resolve(profile / ".elan")


def candidate_user_profiles() -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    raw_values = [
        os.environ.get("USERPROFILE"),
        os.environ.get("HOME"),
        str(Path.home()),
    ]

    for value in raw_values:
        if not value:
            continue
        path = safe_resolve(Path(value).expanduser())
        if path not in seen:
            candidates.append(path)
            seen.add(path)

    if WINDOWS:
        users_dir = Path("C:/Users")
        if users_dir.exists():
            for child in sorted(users_dir.iterdir()):
                if child.is_dir() and child not in seen:
                    candidates.append(child)
                    seen.add(child)

    return candidates


def _winget_tool_candidates(tool_name: str) -> list[Path]:
    if not WINDOWS:
        return []

    relative_prefix = Path(
        "AppData/Local/Microsoft/WinGet/Packages/Lean.Lean_Microsoft.Winget.Source_8wekyb3d8bbwe"
    )
    matches: list[Path] = []
    for profile in candidate_user_profiles():
        base = profile / relative_prefix
        if not base.exists():
            continue
        for candidate in base.glob(f"lean-*-windows/bin/{tool_name}"):
            matches.append(candidate.resolve())
    return matches


def find_elan() -> Path | None:
    cached = find_cached_tool("elan")
    if cached is not None:
        return cached

    names = executable_names("elan")
    for name in names:
        hit = shutil.which(name)
        if hit:
            return Path(hit).resolve()

    relative = Path(".elan/bin/elan.exe" if WINDOWS else ".elan/bin/elan")
    for profile in candidate_user_profiles():
        candidate = profile / relative
        if candidate.exists():
            return candidate.resolve()

    return None


def find_lake() -> Path | None:
    cached = find_cached_tool("lake")
    if cached is not None:
        return cached

    names = ["lake.exe", "lake"] if WINDOWS else ["lake"]
    for name in names:
        hit = shutil.which(name)
        if hit:
            return Path(hit).resolve()

    relative = Path(".elan/bin/lake.exe" if WINDOWS else ".elan/bin/lake")
    for profile in candidate_user_profiles():
        candidate = profile / relative
        if candidate.exists():
            return candidate.resolve()

    for candidate in _winget_tool_candidates("lake.exe"):
        if candidate.exists():
            return candidate

    return None


def find_lean(lake: Path | None = None) -> Path | None:
    cached = find_cached_tool("lean")
    if cached is not None:
        return cached

    names = ["lean.exe", "lean"] if WINDOWS else ["lean"]
    for name in names:
        hit = shutil.which(name)
        if hit:
            return Path(hit).resolve()

    if lake is not None:
        sibling = lake.with_name("lean.exe" if WINDOWS else "lean")
        if sibling.exists():
            return sibling.resolve()

    relative = Path(".elan/bin/lean.exe" if WINDOWS else ".elan/bin/lean")
    for profile in candidate_user_profiles():
        candidate = profile / relative
        if candidate.exists():
            return candidate.resolve()

    for candidate in _winget_tool_candidates("lean.exe"):
        if candidate.exists():
            return candidate

    return None


def subprocess_env_for_tool(tool: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if tool is not None:
        env["PATH"] = f"{tool.parent}{os.pathsep}{env.get('PATH', '')}"

    profile = derive_user_profile_from_tool(tool)
    inferred_elan_home = derive_elan_home_from_tool(tool)
    if profile is not None:
        preferred_home = safe_resolve(profile)
        desired_elan_home = inferred_elan_home or normalize_path(env.get("ELAN_HOME")) or preferred_home / ".elan"
        fallback_home, fallback_elan_home = resolve_fallback_tool_homes(None)
        effective_home = preferred_home if writability_error(preferred_home) is None else fallback_home

        effective_elan_home: Path
        if desired_elan_home is not None and desired_elan_home.exists():
            effective_elan_home = desired_elan_home
        elif desired_elan_home is not None and writability_error(desired_elan_home) is None and prepare_writable_directory(desired_elan_home):
            effective_elan_home = desired_elan_home
        else:
            effective_elan_home = fallback_elan_home

        env["USERPROFILE"] = str(effective_home)
        env["HOME"] = str(effective_home)
        env["ELAN_HOME"] = str(effective_elan_home)

    return env


def git_safe_directories_for_proofs(proofs_dir: Path) -> list[str]:
    directories: list[str] = []
    seen: set[str] = set()

    candidates = [proofs_dir]
    packages_dir = proofs_dir / ".lake" / "packages"
    if packages_dir.exists():
        candidates.extend(path for path in packages_dir.iterdir() if path.is_dir() and (path / ".git").exists())

    for candidate in candidates:
        value = candidate.resolve().as_posix()
        if value not in seen:
            directories.append(value)
            seen.add(value)

    return directories


def add_git_safe_directories(env: dict[str, str], directories: list[str]) -> dict[str, str]:
    if not directories:
        return env

    existing_count = 0
    try:
        existing_count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        existing_count = 0

    existing_values: set[str] = set()
    for index in range(existing_count):
        key = env.get(f"GIT_CONFIG_KEY_{index}")
        value = env.get(f"GIT_CONFIG_VALUE_{index}")
        if key == "safe.directory" and value:
            existing_values.add(value)

    additions = [directory for directory in directories if directory not in existing_values]
    if not additions:
        return env

    for offset, directory in enumerate(additions, start=existing_count):
        env[f"GIT_CONFIG_KEY_{offset}"] = "safe.directory"
        env[f"GIT_CONFIG_VALUE_{offset}"] = directory

    env["GIT_CONFIG_COUNT"] = str(existing_count + len(additions))
    return env


def discover_package_lib_dirs(proofs_dir: Path) -> list[Path]:
    candidates: list[Path] = []

    local_lib = proofs_dir / ".lake" / "build" / "lib" / "lean"
    if local_lib.exists():
        candidates.append(local_lib.resolve())

    packages_dir = proofs_dir / ".lake" / "packages"
    if packages_dir.exists():
        for package_dir in sorted(path for path in packages_dir.iterdir() if path.is_dir()):
            lib_dir = package_dir / ".lake" / "build" / "lib" / "lean"
            if lib_dir.exists():
                candidates.append(lib_dir.resolve())

    return candidates


def build_lean_path(proofs_dir: Path) -> str:
    return os.pathsep.join(str(path) for path in discover_package_lib_dirs(proofs_dir))


def mathlib_module_artifact(proofs_dir: Path) -> Path:
    return proofs_dir / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean"


def _coerce_command_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_readiness_command(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> dict[str, object]:
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
            "stdout": _coerce_command_output(exc.stdout),
            "stderr": (
                f"{_coerce_command_output(exc.stderr).rstrip()}\n"
                f"Command timed out after {timeout_seconds} seconds."
            ).strip(),
            "success": False,
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
        }


def run_verification_readiness_check(
    proofs_dir: Path,
    timeout_seconds: int = READINESS_SMOKE_TIMEOUT_SECONDS,
) -> dict[str, object]:
    target = proofs_dir / READINESS_SMOKE_FILENAME
    lake = find_lake()
    lean = find_lean(lake)
    payload: dict[str, object] = {
        "checked": True,
        "success": False,
        "target": str(target),
        "verification_method": None,
        "methods": [],
    }

    target.write_text(READINESS_SMOKE_SOURCE, encoding="utf-8")
    try:
        relative_target = target.name
        if lake is not None:
            lake_env = subprocess_env_for_tool(lake)
            add_git_safe_directories(lake_env, git_safe_directories_for_proofs(proofs_dir))
            record = _run_readiness_command(
                [str(lake), "env", "lean", relative_target],
                cwd=proofs_dir,
                env=lake_env,
                timeout_seconds=timeout_seconds,
            )
            record["name"] = "lake env lean"
            payload["methods"].append(record)
            if record["success"]:
                payload["success"] = True
                payload["verification_method"] = "lake env lean"
                return payload

        lib_dirs = discover_package_lib_dirs(proofs_dir)
        if lean is not None and lib_dirs:
            lean_env = subprocess_env_for_tool(lean)
            lean_path = build_lean_path(proofs_dir)
            existing_path = lean_env.get("LEAN_PATH", "")
            lean_env["LEAN_PATH"] = f"{lean_path}{os.pathsep}{existing_path}" if existing_path else lean_path
            record = _run_readiness_command(
                [str(lean), relative_target],
                cwd=proofs_dir,
                env=lean_env,
                timeout_seconds=timeout_seconds,
            )
            record["name"] = "direct lean with LEAN_PATH"
            payload["methods"].append(record)
            if record["success"]:
                payload["success"] = True
                payload["verification_method"] = "direct lean with LEAN_PATH fallback"
                return payload

        if payload["methods"]:
            last = payload["methods"][-1]
            payload["error"] = str(last.get("stderr") or last.get("stdout") or "Verification readiness smoke check failed.")
        else:
            payload["error"] = "No usable `lake` or `lean` command was available for the verification readiness smoke check."
        return payload
    finally:
        try:
            target.unlink()
        except OSError:
            pass


def read_text_if_exists(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    stripped = text.strip()
    return stripped or None


def normalize_lean_toolchain(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if ":" in stripped:
        _, stripped = stripped.split(":", 1)
        stripped = stripped.strip()
    if not stripped:
        return None
    if stripped in {"stable", "nightly"} or stripped.startswith("nightly-"):
        return stripped
    return stripped if stripped.startswith("v") else f"v{stripped}"


def mathlib_revision_for_toolchain(value: str | None) -> str | None:
    normalized = normalize_lean_toolchain(value)
    if normalized is None:
        return None
    if normalized in {"stable", "nightly"}:
        return None
    return normalized


def proofs_workspace_status(
    root: Path | None,
    verify_with_tooling: bool = False,
    verify_timeout_seconds: int = READINESS_SMOKE_TIMEOUT_SECONDS,
) -> dict[str, object]:
    if root is None:
        return {
            "workspace_root": None,
            "proofs_dir": None,
            "proofs_exists": False,
            "project_toolchain": None,
            "mathlib_toolchain": None,
            "toolchain_compatible": False,
            "lean_toolchain_exists": False,
            "lakefile_exists": False,
            "proof_scratch_exists": False,
            "mathlib_source_exists": False,
            "mathlib_artifact_exists": False,
            "package_library_paths": [],
            "package_library_path_count": 0,
            "verification_smoke": None,
            "ready_for_search": False,
            "ready_for_verification": False,
            "readiness_level": "incomplete",
        }

    proofs_dir = root / "proofs"
    proof_scratch = proofs_dir / "ProofScratch.lean"
    mathlib_source = proofs_dir / ".lake" / "packages" / "mathlib" / "Mathlib"
    project_toolchain = read_text_if_exists(proofs_dir / "lean-toolchain")
    mathlib_toolchain = read_text_if_exists(proofs_dir / ".lake" / "packages" / "mathlib" / "lean-toolchain")
    normalized_project_toolchain = normalize_lean_toolchain(project_toolchain)
    normalized_mathlib_toolchain = normalize_lean_toolchain(mathlib_toolchain)
    lib_dirs = discover_package_lib_dirs(proofs_dir) if proofs_dir.exists() else []
    proofs_exists = proofs_dir.is_dir()
    lean_toolchain_exists = (proofs_dir / "lean-toolchain").exists()
    lakefile_exists = (proofs_dir / "lakefile.toml").exists()
    proof_scratch_exists = proof_scratch.exists()
    mathlib_source_exists = mathlib_source.exists()
    mathlib_artifact_exists = mathlib_module_artifact(proofs_dir).exists()
    toolchain_compatible = (
        mathlib_source_exists
        and normalized_project_toolchain is not None
        and normalized_mathlib_toolchain is not None
        and normalized_project_toolchain == normalized_mathlib_toolchain
    )
    ready_for_search = proofs_exists and mathlib_source_exists
    ready_for_verification = (
        ready_for_search
        and toolchain_compatible
        and lean_toolchain_exists
        and lakefile_exists
        and proof_scratch_exists
        and mathlib_artifact_exists
        and len(lib_dirs) > 0
    )
    verification_smoke: dict[str, object] | None = None
    if verify_with_tooling and ready_for_verification:
        verification_smoke = run_verification_readiness_check(
            proofs_dir,
            timeout_seconds=verify_timeout_seconds,
        )
        if not bool(verification_smoke.get("success")):
            ready_for_verification = False
    if ready_for_verification:
        readiness_level = "verification-ready"
    elif ready_for_search:
        readiness_level = "search-ready"
    else:
        readiness_level = "incomplete"

    return {
        "workspace_root": str(root),
        "proofs_dir": str(proofs_dir),
        "proofs_exists": proofs_exists,
        "project_toolchain": project_toolchain,
        "mathlib_toolchain": mathlib_toolchain,
        "toolchain_compatible": toolchain_compatible,
        "lean_toolchain_exists": lean_toolchain_exists,
        "lakefile_exists": lakefile_exists,
        "proof_scratch_exists": proof_scratch_exists,
        "mathlib_source_exists": mathlib_source_exists,
        "mathlib_artifact_exists": mathlib_artifact_exists,
        "package_library_paths": [str(path) for path in lib_dirs],
        "package_library_path_count": len(lib_dirs),
        "verification_smoke": verification_smoke,
        "ready_for_search": ready_for_search,
        "ready_for_verification": ready_for_verification,
        "readiness_level": readiness_level,
    }


def run_bootstrap_proofs(
    requested_workspace: Path,
    timeout_seconds: int = 60,
    target: str = "search",
) -> dict[str, object]:
    bootstrap_script = Path(__file__).with_name("bootstrap_proofs.py")
    command = [
        sys.executable,
        str(bootstrap_script),
        "--workspace",
        str(requested_workspace),
        "--scope",
        "shared",
        "--target",
        target,
        "--timeout-seconds",
        str(timeout_seconds),
        "--json",
    ]
    if target != "verify":
        command.append("--skip-verify")
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {
            "success": False,
            "status": "failure",
            "error": "bootstrap_proofs.py returned unreadable output.",
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    payload["bootstrap_exit_code"] = result.returncode
    return payload


def ensure_shared_proofs_workspace(
    requested_workspace: Path,
    timeout_seconds: int = 60,
    require_verification: bool = False,
) -> tuple[Path | None, str | None, dict[str, object], dict[str, object] | None]:
    root, selected_scope = resolve_proofs_workspace(requested_workspace, "shared")
    status = proofs_workspace_status(root, verify_with_tooling=require_verification)
    ready_key = "ready_for_verification" if require_verification else "ready_for_search"
    if bool(status.get(ready_key)):
        return root, selected_scope, status, None

    if require_verification and bool(status.get("ready_for_search")):
        return root, selected_scope, status, None

    # Automatic repair stays on the lightweight search-ready path. Full
    # verification setup is an explicit opt-in via setup_plugin.py.
    bootstrap_payload = run_bootstrap_proofs(
        requested_workspace,
        timeout_seconds=timeout_seconds,
        target="search",
    )
    root, selected_scope = resolve_proofs_workspace(requested_workspace, "shared")
    status = proofs_workspace_status(root, verify_with_tooling=require_verification)
    return root, selected_scope, status, bootstrap_payload


def default_project_name(workspace_root: Path) -> str:
    stem = workspace_root.name or "Workspace"
    parts = [part.capitalize() for part in stem.replace("-", " ").replace("_", " ").split() if part]
    return "".join(parts or ["Workspace"]) + "Proofs"
