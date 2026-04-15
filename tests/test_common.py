from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.dont_write_bytecode = True

SKILL_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills" / "mathlib-ml-arch" / "scripts"
if str(SKILL_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT_DIR))

import common  # noqa: E402


class CommonTests(unittest.TestCase):
    def test_resolve_proofs_workspace_ignores_repo_local_and_uses_shared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requested = root / "workspace"
            requested.mkdir()
            (requested / "proofs").mkdir()
            shared = root / "shared_workspace"

            with patch.object(common, "find_shared_proofs_root", return_value=shared):
                resolved, scope = common.resolve_proofs_workspace(requested, "local")

            self.assertEqual(resolved, shared)
            self.assertEqual(scope, "shared")

    def test_find_lake_prefers_plugin_cached_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / ".codex"
            cached_lake = codex_root / "cache" / common.PLUGIN_SLUG / "toolchains" / "elan" / "toolchains" / "demo" / "bin" / ("lake.exe" if common.WINDOWS else "lake")
            cached_lake.parent.mkdir(parents=True, exist_ok=True)
            cached_lake.write_text("", encoding="utf-8")

            with (
                patch.object(common, "codex_home", return_value=codex_root),
                patch("common.shutil.which", return_value=str(Path(tmp) / "host-lake.exe")),
                patch.object(common, "candidate_user_profiles", return_value=[]),
            ):
                resolved = common.find_lake()

            self.assertEqual(resolved, cached_lake.resolve())

    def test_subprocess_env_uses_plugin_cached_roots_for_cached_toolchain_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / ".codex"
            cached_lake = codex_root / "cache" / common.PLUGIN_SLUG / "toolchains" / "elan" / "toolchains" / "demo" / "bin" / ("lake.exe" if common.WINDOWS else "lake")
            cached_lake.parent.mkdir(parents=True, exist_ok=True)
            cached_lake.write_text("", encoding="utf-8")

            with patch.object(common, "codex_home", return_value=codex_root):
                env = common.subprocess_env_for_tool(cached_lake)

            expected_home = codex_root / "cache" / common.PLUGIN_SLUG / "toolchains" / "home"
            expected_elan = codex_root / "cache" / common.PLUGIN_SLUG / "toolchains" / "elan"
            self.assertEqual(Path(env["HOME"]), expected_home)
            self.assertEqual(Path(env["USERPROFILE"]), expected_home)
            self.assertEqual(Path(env["ELAN_HOME"]), expected_elan)

    def test_shared_workspace_root_falls_back_to_temp_cache_when_codex_home_root_is_not_writable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / ".codex"
            temp_root = Path(tmp) / "temp-root"
            codex_shared = codex_root / "cache" / common.PLUGIN_SLUG / "shared_workspace"
            temp_shared = temp_root / "codex" / common.PLUGIN_SLUG / "shared_workspace"

            def fake_writability_error(path: str | Path | None) -> str | None:
                candidate = Path(path) if path is not None else None
                if candidate is None:
                    return None
                if candidate == codex_shared:
                    return "PermissionError: denied"
                if candidate == temp_shared:
                    return None
                return None

            with (
                patch.object(common, "codex_home", return_value=codex_root),
                patch.object(common, "writability_error", side_effect=fake_writability_error),
                patch("common.tempfile.gettempdir", return_value=str(temp_root)),
            ):
                resolved = common.shared_workspace_root()

            self.assertEqual(resolved, temp_shared.resolve())

    def test_subprocess_env_uses_fallback_locations_when_profile_is_not_writable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / ".codex"
            blocked_profile = Path(tmp) / "blocked-profile"

            def fake_writability_error(path: str | Path | None) -> str | None:
                candidate = Path(path) if path is not None else None
                if candidate is not None and blocked_profile == candidate:
                    return "PermissionError: denied"
                return None

            with (
                patch.object(common, "derive_user_profile_from_tool", return_value=blocked_profile),
                patch.object(common, "codex_home", return_value=codex_root),
                patch.object(common, "writability_error", side_effect=fake_writability_error),
            ):
                env = common.subprocess_env_for_tool(Path(tmp) / "lake.exe")

            expected_home = codex_root / "cache" / common.PLUGIN_SLUG / "toolchains" / "home"
            expected_elan = codex_root / "cache" / common.PLUGIN_SLUG / "toolchains" / "elan"

            self.assertEqual(Path(env["HOME"]), expected_home)
            self.assertEqual(Path(env["USERPROFILE"]), expected_home)
            self.assertEqual(Path(env["ELAN_HOME"]), expected_elan)
            self.assertTrue(expected_home.exists())
            self.assertTrue(expected_elan.exists())

    def test_subprocess_env_uses_temp_fallback_when_codex_home_is_not_writable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocked_profile = Path(tmp) / "blocked-profile"
            blocked_codex = blocked_profile / ".codex"
            temp_root = Path(tmp) / "temp-root"

            def fake_writability_error(path: str | Path | None) -> str | None:
                candidate = Path(path) if path is not None else None
                if candidate is None:
                    return None
                blocked_paths = [blocked_profile, blocked_codex]
                if any(candidate == root or root in candidate.parents for root in blocked_paths):
                    return "PermissionError: denied"
                return None

            with (
                patch.object(common, "derive_user_profile_from_tool", return_value=blocked_profile),
                patch.object(common, "codex_home", return_value=blocked_codex),
                patch.object(common, "writability_error", side_effect=fake_writability_error),
                patch("common.tempfile.gettempdir", return_value=str(temp_root)),
            ):
                env = common.subprocess_env_for_tool(Path(tmp) / "lake.exe")

            expected_home = temp_root / "codex" / common.PLUGIN_SLUG / "toolchains" / "home"
            expected_elan = temp_root / "codex" / common.PLUGIN_SLUG / "toolchains" / "elan"

            self.assertEqual(Path(env["HOME"]), expected_home)
            self.assertEqual(Path(env["USERPROFILE"]), expected_home)
            self.assertEqual(Path(env["ELAN_HOME"]), expected_elan)
            self.assertTrue(expected_home.exists())
            self.assertTrue(expected_elan.exists())


if __name__ == "__main__":
    unittest.main()
