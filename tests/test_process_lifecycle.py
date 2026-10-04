import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cartridges.utils.process_monitor import is_process_group_running, is_process_running


class ProcessLifecycleTests(unittest.TestCase):
    def test_process_is_not_reported_after_it_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "ct-life-test"
            shutil.copy2("/usr/bin/sleep", executable)
            process = subprocess.Popen([executable, "0.15"])
            try:
                self.assertTrue(is_process_running(executable.name))
            finally:
                process.wait(timeout=2)
            time.sleep(0.02)
            self.assertFalse(is_process_running(executable.name))

    def test_process_group_disappears_after_termination(self):
        process = subprocess.Popen(["/usr/bin/sleep", "5"], start_new_session=True)
        try:
            self.assertTrue(is_process_group_running(process.pid))
            process.terminate()
            process.wait(timeout=2)
            self.assertFalse(is_process_group_running(process.pid))
        finally:
            if process.poll() is None:
                process.kill()


if __name__ == "__main__":
    unittest.main()
