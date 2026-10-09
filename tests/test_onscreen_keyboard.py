# test_onscreen_keyboard.py
#
# Copyright 2026 Jolven contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the full on-screen keyboard (Fixes #9).

Headless widget logic: insertion at the cursor, selection-aware
backspace, one-shot Shift and CapsLock. No window is presented, so no
display is needed.
"""

import builtins
import unittest

if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # type: ignore[attr-defined]

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402

from cartridges.onscreen_keyboard import OnscreenKeyboardDialog  # noqa: E402


class OnscreenKeyboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entry = Gtk.Entry()
        self.dialog = OnscreenKeyboardDialog(self.entry)

    def test_pages_cover_letters_numbers_symbols_emojis(self) -> None:
        self.assertEqual(
            set(self.dialog._page_buttons),
            {"letters", "numbers", "symbols", "emojis"},
        )

    def test_insert_respects_cursor_position(self) -> None:
        self.dialog._insert(None, "a")
        self.dialog._insert(None, "b")
        self.entry.set_position(1)
        self.dialog._insert(None, "X")
        self.assertEqual(self.entry.get_text(), "aXb")
        self.assertEqual(self.entry.get_position(), 2)

    def test_backspace_deletes_selection_first(self) -> None:
        self.entry.set_text("aXb")
        self.entry.select_region(0, 2)
        self.dialog._on_backspace()
        self.assertEqual(self.entry.get_text(), "b")

    def test_backspace_at_start_is_noop(self) -> None:
        self.entry.set_text("b")
        self.entry.set_position(0)
        self.dialog._on_backspace()
        self.assertEqual(self.entry.get_text(), "b")

    def test_shift_applies_once_then_releases(self) -> None:
        self.dialog._shift_button.set_active(True)
        self.dialog._on_letter(None, "q")
        self.assertEqual(self.entry.get_text(), "Q")
        self.assertFalse(self.dialog._shift_button.get_active())

    def test_caps_locks_uppercase(self) -> None:
        self.dialog._caps_button.set_active(True)
        self.dialog._on_letter(None, "q")
        self.dialog._on_letter(None, "w")
        self.assertEqual(self.entry.get_text(), "QW")

    def test_emoji_insertion(self) -> None:
        self.entry.set_text("ok")
        self.entry.set_position(2)
        self.dialog._on_insert(None, "😂")
        self.assertEqual(self.entry.get_text(), "ok😂")

    def test_cedilla_and_ene_have_dedicated_keys(self) -> None:
        labels = {base for _, base in self.dialog._letter_buttons}
        self.assertIn("ç", labels)
        self.assertIn("ñ", labels)

    def test_cedilla_and_ene_uppercase_with_caps(self) -> None:
        self.dialog._caps_button.set_active(True)
        self.dialog._on_letter(None, "ç")
        self.dialog._on_letter(None, "ñ")
        self.assertEqual(self.entry.get_text(), "ÇÑ")


if __name__ == "__main__":
    unittest.main()
