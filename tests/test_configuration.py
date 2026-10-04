import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ConfigurationTests(unittest.TestCase):
    def test_game_mode_schema_has_safe_defaults(self):
        schema = ET.parse(
            ROOT / "data" / "page.kramo.Cartridges.gschema.xml.in"
        ).getroot()
        defaults = {
            key.attrib["name"]: key.findtext("default")
            for key in schema.findall(".//key")
        }
        self.assertEqual(defaults["game-mode-use-gamemode"], "false")
        self.assertEqual(defaults["game-mode-use-mangohud"], "false")
        self.assertEqual(defaults["game-mode-enable-vrr"], "false")
        self.assertEqual(defaults["game-mode-monitor"], '""')
        self.assertEqual(defaults["game-mode-audio-output"], '""')
        self.assertEqual(defaults["game-mode-audio-input"], '""')
        self.assertEqual(defaults["thegamesdb-key"], '""')
        self.assertEqual(defaults["thegamesdb"], "false")

    def test_per_game_launch_profile_is_persisted(self):
        tree = ast.parse(
            (ROOT / "cartridges" / "store" / "managers" / "file_manager.py")
            .read_text(encoding="utf-8")
        )
        attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "attrs"
                for target in node.targets
            ):
                attrs.update(
                    item.value
                    for item in node.value.elts
                    if isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                )
        self.assertTrue(
            {
                "game_mode_use_gamemode",
                "game_mode_use_mangohud",
                "launch_working_directory",
                "launch_environment",
                "gamescope_options",
                "fps_limit",
                "game_resolution",
                "scaling_mode",
                "track_process",
                "process_executable",
            }.issubset(attrs)
        )

    def test_thegamesdb_metadata_is_persisted(self):
        tree = ast.parse(
            (ROOT / "cartridges" / "store" / "managers" / "file_manager.py")
            .read_text(encoding="utf-8")
        )
        attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "attrs"
                for target in node.targets
            ):
                attrs.update(
                    item.value
                    for item in node.value.elts
                    if isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                )
        self.assertTrue(
            {
                "tgdb_id",
                "tgdb_checked",
                "tgdb_platform",
                "tgdb_players",
                "tgdb_age_rating",
                "tgdb_coop",
                "tgdb_screenshots",
                "tgdb_fanart",
            }.issubset(attrs)
        )


if __name__ == "__main__":
    unittest.main()
