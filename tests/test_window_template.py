import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class WindowXCloudRefreshTests(unittest.TestCase):
    def test_refresh_widgets_declared_and_in_blueprint(self) -> None:
        py = (ROOT / "cartridges" / "window.py").read_text(encoding="utf-8")
        blp = (ROOT / "data" / "gtk" / "window.blp").read_text(encoding="utf-8")
        for widget in ("xcloud_refresh_button", "xcloud_refresh_spinner"):
            self.assertIn(widget, py)
            self.assertIn(widget, blp)
        self.assertIn("_on_xcloud_refresh_clicked", py)
        self.assertIn("_xcloud_refresh_done", py)
        self.assertIn("_xcloud_refresh_failed", py)

    def test_filter_func_does_not_touch_library_child(self) -> None:
        py = (ROOT / "cartridges" / "window.py").read_text(encoding="utf-8")
        start = py.index("def filter_func(")
        end = py.index("def set_active_game(")
        body = py[start:end]
        self.assertNotIn("set_library_child", body)


if __name__ == "__main__":
    unittest.main()
