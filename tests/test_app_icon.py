import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from cartridges.utils import app_icon


class AppIconTests(unittest.TestCase):
    def test_choices_cover_the_schema_values(self):
        self.assertEqual(app_icon.DEFAULT, "jolven-dpad")
        self.assertIn(app_icon.DEFAULT, app_icon.CHOICES)
        self.assertEqual(set(app_icon.CHOICES), {"jolven-dpad", "jolven-portal"})

    def test_default_choice_removes_the_user_override(self):
        with TemporaryDirectory() as tmp:
            target = app_icon.user_icon_path(Path(tmp))
            target.parent.mkdir(parents=True)
            target.write_text("stale")
            target.with_suffix(".svg.jolven-override").write_text("jolven-portal")
            with mock.patch.object(app_icon.shared, "APP_ID", "io.github.zonaro.Jolven"):
                self.assertEqual(app_icon.apply("jolven-dpad", Path(tmp)), "jolven-dpad")
            self.assertFalse(target.exists())

    def test_default_choice_keeps_the_packaged_icon(self):
        with TemporaryDirectory() as tmp:
            target = app_icon.user_icon_path(Path(tmp))
            target.parent.mkdir(parents=True)
            target.write_text("packaged")
            with mock.patch.object(app_icon.shared, "APP_ID", "io.github.zonaro.Jolven"):
                self.assertEqual(app_icon.apply("jolven-dpad", Path(tmp)), "jolven-dpad")
            self.assertTrue(target.exists())

    def test_unknown_choice_changes_nothing(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.object(
                app_icon.shared.schema, "get_string", return_value="jolven-dpad"
            ):
                self.assertEqual(app_icon.apply("inexistente", Path(tmp)), "jolven-dpad")
            self.assertFalse(app_icon.user_icon_path(Path(tmp)).exists())


if __name__ == "__main__":
    unittest.main()
