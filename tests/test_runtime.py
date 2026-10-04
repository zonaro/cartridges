import unittest

from cartridges.runtime import RuntimeContext, RuntimeMode


class RuntimeContextTests(unittest.TestCase):
    def test_desktop_is_default(self):
        context = RuntimeContext.detect(["cartridges"], {})
        self.assertEqual(context.mode, RuntimeMode.DESKTOP)

    def test_session_flag_selects_dedicated_mode(self):
        context = RuntimeContext.detect(["cartridges", "--session"], {})
        self.assertEqual(context.mode, RuntimeMode.GAME_SESSION)

    def test_windowed_game_mode_is_nested(self):
        context = RuntimeContext.detect(
            ["cartridges", "--game-mode", "--windowed"], {}
        )
        self.assertEqual(context.mode, RuntimeMode.NESTED_GAME_MODE)

    def test_environment_auto_detects_gdm_session(self):
        context = RuntimeContext.detect(
            ["cartridges"], {"CARTRIDGES_GAME_SESSION": "yes"}
        )
        self.assertTrue(context.is_session)

    def test_windowed_without_game_mode_does_not_change_desktop(self):
        context = RuntimeContext.detect(["cartridges", "--windowed"], {})
        self.assertEqual(context.mode, RuntimeMode.DESKTOP)


if __name__ == "__main__":
    unittest.main()
