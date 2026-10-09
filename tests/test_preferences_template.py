# test_preferences_template.py
#
# Copyright 2026 Jolven contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression test: every Template.Child used by the preferences dialog
must exist in the Blueprint source.

A missing id breaks dialog construction, which silently disables every
menu entry that opens Preferences (including Sunshine settings).
Pure file parsing: no GTK, no build directory needed.
"""

import re
import unittest

from pathlib import Path


ROOT = Path(__file__).parents[1]


def template_childs(source: Path) -> set[str]:
    text = source.read_text(encoding="utf-8")
    return set(
        re.findall(r"(\w+)\s*:\s*[\w.]+\s*=\s*Gtk\.Template\.Child\(\)", text)
    )


def blueprint_ids(source: Path) -> set[str]:
    ids: set[str] = set()
    pattern = re.compile(r"^\s*(?:\[suffix\]|Adw\.|Gtk\.)?.*?(\w+)\s*\{")
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "using", "menu ", "item ")):
            continue
        stripped = re.sub(r"^\w+:\s*", "", stripped)
        match = re.match(r"(?:\w[\w.]*\s+)?([A-Za-z_][\w]*)\s*\{", stripped)
        if match:
            ids.add(match.group(1))
    return ids


class PreferencesTemplateTests(unittest.TestCase):
    def test_all_childs_exist_in_blueprint(self) -> None:
        childs = template_childs(ROOT / "cartridges" / "preferences.py")
        self.assertGreater(len(childs), 100)
        ids = blueprint_ids(ROOT / "data" / "gtk" / "preferences.blp")
        missing = sorted(childs - ids)
        self.assertEqual(missing, [])

    def test_sunshine_page_exists(self) -> None:
        blp = (ROOT / "data" / "gtk" / "preferences.blp").read_text(
            encoding="utf-8"
        )
        self.assertIn('name: "sunshine";', blp)


if __name__ == "__main__":
    unittest.main()
