import unittest

from cartridges.gpu_screen_recorder import find_desktop_application


class _Application:
    def __init__(self, desktop_id):
        self.desktop_id = desktop_id

    def get_id(self):
        return self.desktop_id


class GpuScreenRecorderTests(unittest.TestCase):
    def test_finds_official_desktop_application(self):
        unrelated = _Application("example.desktop")
        recorder = _Application("com.dec05eba.gpu_screen_recorder.desktop")

        self.assertIs(
            find_desktop_application([unrelated, recorder]),
            recorder,
        )

    def test_missing_application_is_optional(self):
        self.assertIsNone(find_desktop_application([_Application("example.desktop")]))


if __name__ == "__main__":
    unittest.main()
