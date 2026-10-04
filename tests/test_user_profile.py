import os
import pwd
import tempfile
import unittest
from pathlib import Path

from cartridges.user_profile import current_user_profile


class UserProfileTests(unittest.TestCase):
    def test_current_account_uses_linux_identity(self):
        entry = pwd.getpwuid(os.getuid())
        profile = current_user_profile()
        self.assertEqual(profile.username, entry.pw_name)
        self.assertTrue(profile.display_name)

    def test_face_file_is_used_as_fallback_avatar(self):
        entry = pwd.getpwuid(os.getuid())
        with tempfile.TemporaryDirectory() as directory:
            face = Path(directory) / ".face"
            face.write_bytes(b"avatar")
            profile = current_user_profile(
                username=entry.pw_name,
                home=Path(directory),
                accounts_root=Path(directory) / "accounts",
            )
            self.assertEqual(profile.avatar, face)


if __name__ == "__main__":
    unittest.main()
