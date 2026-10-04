import unittest

from cartridges.utils.process_monitor import flatpak_id_from_command, steam_appid_from_command


class SteamBackendTests(unittest.TestCase):
    def test_native_steam_uri(self):
        self.assertEqual(
            steam_appid_from_command("xdg-open steam://rungameid/620"), "620"
        )

    def test_case_insensitive_uri(self):
        self.assertEqual(
            steam_appid_from_command("xdg-open STEAM://RUNGAMEID/10"), "10"
        )

    def test_non_steam_launcher_is_not_misidentified(self):
        self.assertEqual(
            steam_appid_from_command("flatpak run com.heroicgameslauncher.hgl"), ""
        )

    def test_flatpak_backend(self):
        self.assertEqual(
            flatpak_id_from_command("flatpak run com.heroicgameslauncher.hgl"),
            "com.heroicgameslauncher.hgl",
        )


if __name__ == "__main__":
    unittest.main()
