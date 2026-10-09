import unittest

from cartridges.launchers import (
    is_twintail_command,
    resolve_steam_command,
    resolve_twintail_command,
)


class LauncherTests(unittest.TestCase):
    def test_native_steam_is_preferred(self):
        command = resolve_steam_command(
            "xdg-open steam://rungameid/620",
            find_program=lambda name: "/usr/bin/steam" if name == "steam" else None,
        )
        self.assertEqual(command, '"/usr/bin/steam" -silent "steam://rungameid/620"')

    def test_flatpak_steam_fallback(self):
        command = resolve_steam_command(
            "xdg-open steam://rungameid/10",
            find_program=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None,
            has_flatpak=lambda app_id: app_id == "com.valvesoftware.Steam",
        )
        self.assertEqual(
            command,
            'flatpak run com.valvesoftware.Steam -silent "steam://rungameid/10"',
        )

    def test_unknown_backend_preserves_url_handler(self):
        original = "xdg-open steam://rungameid/10"
        self.assertEqual(
            resolve_steam_command(original, find_program=lambda _name: None), original
        )


class TwintailCommandTests(unittest.TestCase):
    def test_host_binary_is_preferred(self):
        command = resolve_twintail_command(
            "01a0bab3-0732-78d0-bc0f-cf1137d5a516",
            find_program=lambda name: "/usr/bin/twintaillauncher"
            if name == "twintaillauncher"
            else None,
            has_flatpak=lambda _app_id: True,
        )
        self.assertEqual(
            command,
            "twintaillauncher --install=01a0bab3-0732-78d0-bc0f-cf1137d5a516",
        )

    def test_flatpak_fallback_without_host_binary(self):
        command = resolve_twintail_command(
            "01a0bab3-0732-78d0-bc0f-cf1137d5a516",
            find_program=lambda _name: None,
            has_flatpak=lambda app_id: app_id == "app.twintaillauncher.ttl",
        )
        self.assertEqual(
            command,
            "flatpak run app.twintaillauncher.ttl "
            "--install=01a0bab3-0732-78d0-bc0f-cf1137d5a516",
        )

    def test_no_backend_keeps_host_form(self):
        self.assertEqual(
            resolve_twintail_command(
                "some-id",
                find_program=lambda _name: None,
                has_flatpak=lambda _app_id: False,
            ),
            "twintaillauncher --install=some-id",
        )

    def test_is_twintail_command(self):
        self.assertTrue(
            is_twintail_command("twintaillauncher --install=some-id")
        )
        self.assertTrue(
            is_twintail_command(
                "twintaillauncher --install some-id"
            )
        )
        self.assertTrue(
            is_twintail_command(
                "flatpak run app.twintaillauncher.ttl --install=some-id"
            )
        )
        self.assertFalse(is_twintail_command("legendary launch AppName"))
        self.assertFalse(is_twintail_command(""))


if __name__ == "__main__":
    unittest.main()
