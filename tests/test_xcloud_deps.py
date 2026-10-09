import unittest
from pathlib import Path
from unittest.mock import patch

import cartridges.xcloud_deps as deps


class FakeRegistry:
    def __init__(self, available):
        self._available = set(available)

    def find_feature(self, name, _kind):
        return object() if name in self._available else None


class FakeGst:
    ElementFactory = object()
    available = set()

    @staticmethod
    def init(_args):
        return None

    class Registry:
        @staticmethod
        def get():
            return FakeRegistry(FakeGst.available)


class XCloudDepsTests(unittest.TestCase):
    def test_missing_reports_absent_elements(self):
        with patch.object(deps, "_Gst", FakeGst):
            FakeGst.available = {"nicesrc"}
            self.assertEqual(
                deps.missing_elements(("nicesrc", "nicesink")), ["nicesink"]
            )

    def test_nothing_missing_when_all_present(self):
        with patch.object(deps, "_Gst", FakeGst):
            FakeGst.available = {"nicesrc", "nicesink"}
            self.assertEqual(
                deps.missing_elements(("nicesrc", "nicesink")), []
            )

    def test_everything_missing_without_gst(self):
        with patch.object(deps, "_Gst", None):
            self.assertEqual(
                deps.missing_elements(("nicesrc", "nicesink")),
                ["nicesrc", "nicesink"],
            )

    def test_default_probe_requires_sctp_and_a_decoder(self):
        with patch.object(deps, "_Gst", FakeGst):
            FakeGst.available = set(deps.REQUIRED_ELEMENTS)
            self.assertEqual(deps.missing_elements(), ["h264-decoder"])
            FakeGst.available.add("openh264dec")
            self.assertEqual(deps.missing_elements(), [])

    def test_helper_found_in_repo_session_dir(self):
        with patch.object(deps, "_shared_libexecdir", return_value=None):
            path = deps._helper_path()
        self.assertIsNotNone(path)
        self.assertTrue(str(path).endswith("jolven-xcloud-deps.in"))

    def test_install_without_helper_returns_false(self):
        with patch.object(deps, "_helper_path", return_value=None):
            called = []
            self.assertFalse(deps.install_async(called.append))
            self.assertEqual(called, [])

    def test_output_streams_line_by_line(self):
        import io
        import threading

        class FakeStdout(io.StringIO):
            pass

        class FakeProc:
            def __init__(self):
                self.stdout = io.StringIO("linha um\n\nlinha dois\n")
                self.returncode = 0

            def wait(self, timeout=None):
                self.stdout.seek(0)
                return self.returncode

        seen_lines = []
        results = []
        started = threading.Event()

        real_popen = deps.subprocess.Popen

        def fake_popen(*args, **kwargs):
            started.set()
            return FakeProc()

        with (
            patch.object(deps, "_helper_path", return_value="/tmp/helper"),
            patch.object(deps.shutil, "which", return_value="/usr/bin/pkexec"),
            patch.object(deps.subprocess, "Popen", side_effect=fake_popen),
        ):
            self.assertTrue(
                deps.install_async(
                    lambda ok, msg: results.append((ok, msg)),
                    on_output=seen_lines.append,
                )
            )
            self.assertTrue(started.wait(timeout=5))
            deadline = 50
            while not results and deadline > 0:
                import time as _time

                _time.sleep(0.1)
                deadline -= 1
        self.assertEqual(seen_lines, ["linha um", "linha dois"])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][0])

    def test_build_restart_command_relaunches_argv(self):
        cmd = deps.build_restart_command(["/home/u/.local/bin/jolven", "--game-mode"])
        self.assertEqual(cmd[:3], ["setsid", "nohup", "sh"])
        self.assertIn("sleep 2", cmd[4])
        self.assertIn("/home/u/.local/bin/jolven", cmd[4])
        self.assertIn("--game-mode", cmd[4])

    def test_policy_file_is_valid_xml(self):
        import xml.dom.minidom

        policy = (
            Path(__file__).parents[1]
            / "data"
            / "io.github.zonaro.Jolven.XCloudDeps.policy.in"
        )
        dom = xml.dom.minidom.parse(str(policy))
        actions = dom.getElementsByTagName("action")
        self.assertEqual(len(actions), 1)
        self.assertEqual(
            actions[0].getAttribute("id"),
            "io.github.zonaro.Jolven.install-xcloud-deps",
        )

    def test_helper_rejects_arbitrary_operations(self):
        helper = (
            Path(__file__).parents[1] / "session" / "jolven-xcloud-deps.in"
        ).read_text()
        self.assertIn('"$action" != install-xcloud-deps', helper)
        self.assertIn("EUID", helper)
        self.assertIn("libnice-gstreamer1", helper)


if __name__ == "__main__":
    unittest.main()
