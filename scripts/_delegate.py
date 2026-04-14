from __future__ import annotations

import runpy
import sys
from pathlib import Path


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def skill_scripts_dir() -> Path:
    return plugin_root() / "skills" / "mathlib-ml-arch" / "scripts"


def run_skill_entry(script_name: str) -> None:
    target_dir = skill_scripts_dir()
    target = target_dir / script_name
    if not target.exists():
        raise SystemExit(f"Skill entrypoint not found: {target}")

    sys.path.insert(0, str(target_dir))
    sys.argv[0] = str(target)
    runpy.run_path(str(target), run_name="__main__")
