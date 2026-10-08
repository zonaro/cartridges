# test_sunshine.py
#
# Copyright 2026 Jolven contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the Sunshine export (no GTK involved)."""

import json
import unittest

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cartridges.utils.sunshine import (
    SunshineError,
    add_game,
    build_app_entry,
    build_command,
    is_eligible,
    read_catalog,
    resolve_apps_path,
    sync_library,
    upsert_app,
)


def make_game(**overrides: Any) -> SimpleNamespace:
    game = SimpleNamespace(
        name="Test Game",
        game_id="steam_123",
        source="steam_123",
        base_source="steam",
        executable="steam steam://rungameid/123",
        launch_working_directory="",
        launch_environment={},
        removed=False,
    )
    for key, value in overrides.items():
        setattr(game, key, value)
    return game


class EligibilityTests(unittest.TestCase):
    def test_normal_game_is_eligible(self) -> None:
        self.assertTrue(is_eligible(make_game()))

    def test_none_is_not_eligible(self) -> None:
        self.assertFalse(is_eligible(None))

    def test_xcloud_sources_are_skipped(self) -> None:
        for source in ("xcloud_free", "xcloud_gamepass", "xcloud_owned"):
            with self.subTest(source=source):
                game = make_game(source=source, base_source="xcloud")
                self.assertFalse(is_eligible(game))

    def test_blank_executable_is_skipped(self) -> None:
        self.assertFalse(is_eligible(make_game(executable="   ")))

    def test_removed_game_is_skipped(self) -> None:
        self.assertFalse(is_eligible(make_game(removed=True)))


class CommandTests(unittest.TestCase):
    def test_bare_executable_without_env(self) -> None:
        game = make_game(executable="lutris lutris:rungame/doom")
        self.assertEqual(build_command(game), "lutris lutris:rungame/doom")

    def test_environment_is_prefixed_with_quoting(self) -> None:
        game = make_game(
            executable="/opt/game/run.sh --fullscreen",
            launch_environment={"FOO": "a b", "BAR": "plain"},
        )
        self.assertEqual(
            build_command(game),
            "env FOO='a b' BAR=plain /opt/game/run.sh --fullscreen",
        )

    def test_invalid_env_keys_are_dropped(self) -> None:
        game = make_game(
            executable="run.sh",
            launch_environment={"1BAD": "x", "": "y", "GOOD_KEY": "z"},
        )
        self.assertEqual(build_command(game), "env GOOD_KEY=z run.sh")


class CatalogTests(unittest.TestCase):
    def test_missing_file_yields_empty_catalog(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            catalog = read_catalog(Path(tmp) / "apps.json")
        self.assertEqual(catalog, {"env": {}, "apps": []})

    def test_upsert_add_update_unchanged(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "apps.json"
            entry = {
                "name": "Doom",
                "cmd": "doom --fast",
                "output": "",
                "detached": [],
                "prep-cmd": [],
                "working-dir": "",
                "image-path": "",
                "exclude-global-prep-cmd": False,
                "elevated": False,
                "auto-detach": True,
                "wait-all": True,
                "exit-timeout": 5,
            }
            self.assertEqual(upsert_app(path, entry), "added")
            self.assertEqual(upsert_app(path, dict(entry)), "unchanged")
            changed = dict(entry, cmd="doom --ultra")
            self.assertEqual(upsert_app(path, changed), "updated")
            catalog = read_catalog(path)
            self.assertEqual(len(catalog["apps"]), 1)
            self.assertEqual(catalog["apps"][0]["cmd"], "doom --ultra")

    def test_upsert_preserves_foreign_entries_and_env(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "apps.json"
            foreign = {"name": "Desktop", "cmd": "xterm", "custom": True}
            path.write_text(
                json.dumps({"env": {"PATH": "/usr/bin"}, "apps": [foreign]}),
                encoding="utf-8",
            )
            upsert_app(path, {"name": "Doom", "cmd": "doom"})
            catalog = read_catalog(path)
            self.assertEqual(catalog["env"], {"PATH": "/usr/bin"})
            self.assertEqual(len(catalog["apps"]), 2)
            self.assertIn(foreign, catalog["apps"])

    def test_invalid_json_raises_without_overwrite(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "apps.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(SunshineError):
                upsert_app(path, {"name": "Doom", "cmd": "doom"})
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_entry_without_name_or_cmd_raises(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "apps.json"
            with self.assertRaises(SunshineError):
                upsert_app(path, {"name": "", "cmd": "doom"})
            with self.assertRaises(SunshineError):
                upsert_app(path, {"name": "Doom", "cmd": "  "})


class SyncTests(unittest.TestCase):
    def test_add_game_returns_entry_name(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "apps.json")
            name = add_game(make_game(name="Quake"), custom_path=path)
            self.assertEqual(name, "Quake")
            apps = read_catalog(Path(path))["apps"]
            self.assertEqual([app["name"] for app in apps], ["Quake"])

    def test_add_game_rejects_ineligible(self) -> None:
        with self.assertRaises(SunshineError):
            add_game(
                make_game(source="xcloud_free", base_source="xcloud"),
                custom_path="/nonexistent/should/not/be/created.json",
            )

    def test_sync_library_counts(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "apps.json")
            games = [
                make_game(name="One"),
                make_game(name="Two"),
                make_game(
                    name="Cloud",
                    source="xcloud_free",
                    base_source="xcloud",
                ),
                make_game(name="NoExe", executable=""),
            ]
            first = sync_library(games, custom_path=path)
            self.assertEqual(first["added"], 2)
            self.assertEqual(first["skipped"], 2)
            second = sync_library(games, custom_path=path)
            self.assertEqual(second["unchanged"], 2)
            self.assertEqual(second["skipped"], 2)

    def test_resolve_apps_path(self) -> None:
        default = resolve_apps_path()
        self.assertEqual(default.name, "apps.json")
        self.assertEqual(default.parent.name, "sunshine")
        custom = resolve_apps_path("~/custom/apps.json")
        self.assertTrue(str(custom).endswith("custom/apps.json"))
        self.assertNotIn("~", str(custom))

    def test_build_app_entry_shape(self) -> None:
        entry = build_app_entry(make_game(name="Doom"), "/cover.png")
        self.assertEqual(entry["name"], "Doom")
        self.assertEqual(entry["image-path"], "/cover.png")
        self.assertTrue(entry["auto-detach"])
        self.assertEqual(entry["exit-timeout"], 5)


if __name__ == "__main__":
    unittest.main()
