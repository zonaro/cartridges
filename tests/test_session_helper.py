import subprocess
import unittest
from pathlib import Path


class SessionHelperTests(unittest.TestCase):
    def test_login_session_name_uses_distribution(self):
        helper = Path(__file__).parents[1] / "session" / "cartridges-game-mode-setup.in"
        result = subprocess.run(
            [helper, "name"], capture_output=True, encoding="utf-8", check=True
        )
        self.assertTrue(result.stdout.strip().endswith(" Gaming Mode"))
        if Path("/etc/fedora-release").exists():
            self.assertEqual(result.stdout.strip(), "Fedora Gaming Mode")


if __name__ == "__main__":
    unittest.main()
