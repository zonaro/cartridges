import unittest

from cartridges.utils.waydroid import package_id_from_exec


class WaydroidSourceTests(unittest.TestCase):
    def test_extracts_package_from_generated_desktop_command(self):
        self.assertEqual(
            package_id_from_exec("waydroid app launch com.example.game"),
            "com.example.game",
        )

    def test_accepts_absolute_waydroid_path(self):
        self.assertEqual(
            package_id_from_exec("/usr/bin/waydroid app launch com.example.game"),
            "com.example.game",
        )

    def test_rejects_other_and_incomplete_commands(self):
        self.assertIsNone(package_id_from_exec("waydroid app intent example"))
        self.assertIsNone(package_id_from_exec("waydroid app launch"))
        self.assertIsNone(package_id_from_exec("waydroid 'unterminated"))


if __name__ == "__main__":
    unittest.main()
