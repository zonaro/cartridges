import os
import unittest
from pathlib import Path
from unittest import mock

from cartridges.utils import global_shortcut


class GlobalShortcutTests(unittest.TestCase):
    def test_kde_keycode_is_meta_plus_g(self):
        self.assertEqual(
            global_shortcut.KDE_KEYCODE_G, 0x10000000 | ord("G")
        )

    def test_desktop_family_detection(self):
        gnome_cases = ("GNOME", "ubuntu:GNOME", "Unity", "Budgie", "Cinnamon")
        kde_cases = ("KDE", "plasma", "LXQt")
        for desktop in gnome_cases:
            self.assertEqual(
                global_shortcut.desktop_family_from_string(desktop), "gnome"
            )
        for desktop in kde_cases:
            self.assertEqual(
                global_shortcut.desktop_family_from_string(desktop), "kde"
            )
        self.assertIsNone(global_shortcut.desktop_family_from_string("XFCE"))
        self.assertIsNone(global_shortcut.desktop_family_from_string(""))

    def test_resolve_command_prefers_local_install(self):
        with mock.patch.object(
            Path, "is_file", lambda self: str(self).endswith(".local/bin/cartridges")
        ), mock.patch("os.access", return_value=True):
            self.assertTrue(
                global_shortcut.resolve_command().endswith(".local/bin/cartridges")
            )

    def test_resolve_command_honours_env_override(self):
        with mock.patch.dict(os.environ, {"CARTRIDGES_COMMAND": "flatpak run X"}):
            self.assertEqual(global_shortcut.resolve_command(), "flatpak run X")

    def test_shortcut_list_helpers_are_idempotent(self):
        path = global_shortcut.GNOME_SHORTCUT_PATH
        self.assertEqual(global_shortcut.with_path([], path), [path])
        self.assertEqual(global_shortcut.with_path([path], path), [path])
        self.assertEqual(
            global_shortcut.with_path(["a", path], path), ["a", path]
        )
        self.assertEqual(global_shortcut.without_path(["a", path], path), ["a"])
        self.assertEqual(global_shortcut.without_path([], path), [])

    def test_kde_desktop_entry_carries_discovery_key(self):
        entry = global_shortcut.kde_desktop_entry("/usr/bin/cartridges")
        self.assertIn("Exec=/usr/bin/cartridges", entry)
        self.assertIn("X-KDE-Shortcuts=Meta+G", entry)
        self.assertIn("NoDisplay=true", entry)


if __name__ == "__main__":
    unittest.main()
