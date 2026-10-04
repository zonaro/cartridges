import unittest

from cartridges.controller import (
    ControllerAction,
    action_for_button,
    name_for_button,
    trigger_for_button,
)


class ControllerTests(unittest.TestCase):
    def test_semantic_buttons(self):
        self.assertEqual(action_for_button(304), ControllerAction.BACK)
        self.assertEqual(action_for_button(305), ControllerAction.CONFIRM)
        self.assertEqual(action_for_button(307), ControllerAction.SEARCH)
        self.assertEqual(action_for_button(308), ControllerAction.GAME_MENU)
        self.assertEqual(action_for_button(315), ControllerAction.MAIN_MENU)
        self.assertEqual(action_for_button(316), ControllerAction.GUIDE)

    def test_unknown_button_is_ignored(self):
        self.assertIsNone(action_for_button(999))

    def test_xbox_names_are_available_to_the_tester(self):
        self.assertEqual(name_for_button(304), "B")
        self.assertEqual(name_for_button(305), "A")
        self.assertEqual(name_for_button(307), "Y")
        self.assertEqual(name_for_button(308), "X")
        self.assertEqual(name_for_button(999), "Button 999")

    def test_triggers_are_not_regular_buttons(self):
        self.assertEqual(trigger_for_button(312), "LT")
        self.assertEqual(trigger_for_button(313), "RT")
        self.assertIsNone(action_for_button(312))
        self.assertIsNone(action_for_button(313))


if __name__ == "__main__":
    unittest.main()
