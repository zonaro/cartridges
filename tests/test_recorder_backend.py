import unittest

from cartridges.recorder.backend import (
    RECORDER_BINARY,
    RecorderSettings,
    build_argv,
    find_backend,
)
from cartridges.recorder.service import RecorderService


class BuildArgvTests(unittest.TestCase):
    def test_default_settings_map_to_flags(self):
        self.assertEqual(
            build_argv(RecorderSettings()),
            [
                RECORDER_BINARY,
                "-w", "portal",
                "-c", "mp4",
                "-k", "auto",
                "-q", "very_high",
                "-bm", "auto",
                "-f", "60",
                "-cursor", "yes",
                "-a", "default_output",
                "-ac", "opus",
                "-ab", "128",
                "-o", "~/Videos/Jolven",
            ],
        )

    def test_cursor_disabled_emits_no(self):
        argv = build_argv(RecorderSettings(cursor=False))
        self.assertEqual(argv[argv.index("-cursor") + 1], "no")

    def test_empty_scale_is_omitted(self):
        self.assertNotIn("-s", build_argv(RecorderSettings()))

    def test_scale_is_mapped(self):
        argv = build_argv(RecorderSettings(scale="1920x1080"))
        self.assertEqual(argv[argv.index("-s") + 1], "1920x1080")

    def test_audio_sources_repeat(self):
        argv = build_argv(
            RecorderSettings(audio_sources=("default_output", "default_input"))
        )
        self.assertEqual(
            [argv[i + 1] for i, token in enumerate(argv) if token == "-a"],
            ["default_output", "default_input"],
        )

    def test_replay_flags_present_only_with_duration(self):
        argv = build_argv(RecorderSettings(replay_seconds=60, replay_storage="disk"))
        self.assertEqual(argv[argv.index("-r") + 1], "60")
        self.assertEqual(argv[argv.index("-replay-storage") + 1], "disk")
        self.assertNotIn("-r", build_argv(RecorderSettings()))

    def test_stream_url_becomes_output(self):
        argv = build_argv(RecorderSettings(stream_url="rtmp://example/live"))
        self.assertEqual(argv[-1], "rtmp://example/live")

    def test_output_override_wins(self):
        argv = build_argv(
            RecorderSettings(stream_url="rtmp://example/live"),
            output="/tmp/clip.mp4",
        )
        self.assertEqual(argv[-1], "/tmp/clip.mp4")

    def test_program_is_overridable(self):
        argv = build_argv(RecorderSettings(), program="/usr/bin/gpu-screen-recorder")
        self.assertEqual(argv[0], "/usr/bin/gpu-screen-recorder")


class FindBackendTests(unittest.TestCase):
    def test_found_binary_returns_path(self):
        self.assertEqual(
            find_backend(lambda name: f"/usr/bin/{name}"),
            "/usr/bin/gpu-screen-recorder",
        )

    def test_missing_binary_returns_none(self):
        self.assertIsNone(find_backend(lambda name: None))


class RecorderServiceTests(unittest.TestCase):
    def test_missing_binary_is_a_noop(self):
        service = RecorderService(find_program=lambda name: None)
        self.assertFalse(service.is_available())
        self.assertFalse(service.start())
        self.assertFalse(service.stop())
        self.assertFalse(service.save_replay())
        self.assertFalse(service.toggle_pause())
        self.assertFalse(service.is_running())

    def test_available_backend_reports_readiness(self):
        service = RecorderService(
            find_program=lambda name: "/usr/bin/gpu-screen-recorder"
        )
        self.assertTrue(service.is_available())
        self.assertFalse(service.is_running())


if __name__ == "__main__":
    unittest.main()
