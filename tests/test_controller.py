import unittest

from cartridges.controller import (
    ControllerAction,
    action_for_button,
    hat_direction,
    name_for_button,
    stick_direction,
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

    def test_hat_directions(self):
        self.assertIsNone(hat_direction(16, 0))
        self.assertIsNone(hat_direction(17, 0))
        self.assertEqual(hat_direction(16, -1), "left")
        self.assertEqual(hat_direction(16, 1), "right")
        self.assertEqual(hat_direction(17, -1), "up")
        self.assertEqual(hat_direction(17, 1), "down")
        self.assertIsNone(hat_direction(99, 1))

    def test_stick_direction_threshold(self):
        self.assertEqual(stick_direction(0.0), 0)
        self.assertEqual(stick_direction(-0.54), 0)
        self.assertEqual(stick_direction(0.54), 0)
        self.assertEqual(stick_direction(-0.56), -1)
        self.assertEqual(stick_direction(0.56), 1)
        self.assertEqual(stick_direction(-1.0), -1)
        self.assertEqual(stick_direction(1.0), 1)


if __name__ == "__main__":
    unittest.main()
