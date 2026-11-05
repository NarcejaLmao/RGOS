"""sshctl: the custom root password is read exactly as typed, whatever editor saved the file."""
import os
import sys
import unittest

from tests import helpers

sys.path.insert(0, helpers.RGOS)
try:
    import sshctl
finally:
    sys.path.remove(helpers.RGOS)


class ReadPasswordTests(unittest.TestCase):
    def read(self, raw):
        p = os.path.join(helpers.sandbox(), "ssh-password.txt")
        with open(p, "wb") as f:
            f.write(raw)
        return sshctl.read_password(p)

    def test_plain_and_line_endings(self):
        for raw in (b"hunter2", b"hunter2\n", b"hunter2\r\n", b"  hunter2  \nignored\n"):
            self.assertEqual(self.read(raw), "hunter2", raw)

    def test_utf8_bom_is_not_part_of_the_password(self):    # Windows Notepad adds one; it used to end up in the hash
        self.assertEqual(self.read(b"\xef\xbb\xbfhunter2\r\n"), "hunter2")

    def test_non_ascii_password_survives(self):
        self.assertEqual(self.read("pässwörd\n".encode("utf-8")), "pässwörd")

    def test_empty_file_gives_an_empty_password_and_missing_file_none(self):
        self.assertEqual(self.read(b""), "")
        self.assertIsNone(sshctl.read_password(os.path.join(helpers.sandbox(), "nope.txt")))


if __name__ == "__main__":
    unittest.main()
