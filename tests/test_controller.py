import unittest

from cartridges.controller import (
    BUTTON_HOME,
    BUTTON_MODE,
    ControllerAction,
    ControllerLayout,
    action_for_button,
    detect_controller_layout,
    is_tester_exit_chord,
    hat_direction,
    labels_for_layout,
    name_for_button,
    opens_game_overlay,
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
        self.assertEqual(action_for_button(172), ControllerAction.HOME)

    def test_unknown_button_is_ignored(self):
        self.assertIsNone(action_for_button(999))

    def test_xbox_names_are_available_to_the_tester(self):
        self.assertEqual(name_for_button(304), "B")
        self.assertEqual(name_for_button(305), "A")
        self.assertEqual(name_for_button(307), "Y")
        self.assertEqual(name_for_button(308), "X")
        self.assertEqual(name_for_button(999), "Button 999")

    def test_game_overlay_button_preference(self):
        self.assertTrue(opens_game_overlay(BUTTON_MODE, "mode"))
        self.assertFalse(opens_game_overlay(BUTTON_HOME, "mode"))
        self.assertTrue(opens_game_overlay(BUTTON_HOME, "home"))
        self.assertFalse(opens_game_overlay(BUTTON_MODE, "home"))
        self.assertTrue(opens_game_overlay(BUTTON_MODE, "both"))
        self.assertTrue(opens_game_overlay(BUTTON_HOME, "both"))
        self.assertFalse(opens_game_overlay(BUTTON_MODE, "invalid"))

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

    def test_detect_xbox_layout(self):
        self.assertEqual(
            detect_controller_layout("Microsoft X-Box 360 pad"),
            ControllerLayout.XBOX,
        )
        self.assertEqual(
            detect_controller_layout("Xbox Wireless Controller"),
            ControllerLayout.XBOX,
        )
        self.assertEqual(
            detect_controller_layout("microsoft xbox one"),
            ControllerLayout.XBOX,
        )

    def test_detect_playstation_layout(self):
        self.assertEqual(
            detect_controller_layout("Sony DualSense Wireless Controller"),
            ControllerLayout.PLAYSTATION,
        )
        self.assertEqual(
            detect_controller_layout("PLAYSTATION(R)3 Controller"),
            ControllerLayout.PLAYSTATION,
        )
        self.assertEqual(
            detect_controller_layout("ps5 controller"),
            ControllerLayout.PLAYSTATION,
        )
        self.assertEqual(
            detect_controller_layout("DualShock 4"),
            ControllerLayout.PLAYSTATION,
        )

    def test_detect_generic_layout(self):
        self.assertEqual(
            detect_controller_layout("Generic USB Joystick"),
            ControllerLayout.GENERIC,
        )
        self.assertEqual(detect_controller_layout(""), ControllerLayout.GENERIC)
        self.assertEqual(detect_controller_layout(None), ControllerLayout.GENERIC)

    def test_generic_labels_match_xbox_names(self):
        self.assertEqual(
            labels_for_layout(ControllerLayout.GENERIC),
            labels_for_layout(ControllerLayout.XBOX),
        )
        labels = labels_for_layout(ControllerLayout.GENERIC)
        self.assertEqual(labels[305], "A")
        self.assertEqual(labels[BUTTON_MODE], "Mode")
        self.assertEqual(labels[BUTTON_HOME], "Home")

    def test_playstation_labels(self):
        labels = labels_for_layout(ControllerLayout.PLAYSTATION)
        self.assertEqual(labels[304], "Circle")
        self.assertEqual(labels[305], "Cross")
        self.assertEqual(labels[307], "Triangle")
        self.assertEqual(labels[308], "Square")
        self.assertEqual(labels[310], "L1")
        self.assertEqual(labels[311], "R1")
        self.assertEqual(labels[312], "L2")
        self.assertEqual(labels[313], "R2")
        self.assertEqual(labels[314], "Share")
        self.assertEqual(labels[315], "Options")
        self.assertEqual(labels[316], "PS")
        self.assertEqual(labels[BUTTON_HOME], "Home")
        self.assertEqual(labels[317], "L3")
        self.assertEqual(labels[318], "R3")

    def test_labels_for_layout_returns_fresh_dict(self):
        first = labels_for_layout(ControllerLayout.XBOX)
        first[304] = "mutated"

    def test_tester_exit_chord_needs_view_and_menu(self):
        self.assertTrue(is_tester_exit_chord({314, 315}))
        self.assertTrue(is_tester_exit_chord({304, 314, 315}))
        self.assertFalse(is_tester_exit_chord({314}))
        self.assertFalse(is_tester_exit_chord({315}))
        self.assertFalse(is_tester_exit_chord(set()))
        self.assertEqual(labels_for_layout(ControllerLayout.XBOX)[304], "B")


if __name__ == "__main__":
    unittest.main()
