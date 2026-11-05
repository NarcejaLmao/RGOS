"""filemanager: the protected boot files stay protected, whatever the name looks like."""
import os
import shutil
import sys
import tempfile
import unittest

from tests import helpers

sys.path.insert(0, helpers.RGOS)
try:
    import filemanager
finally:
    sys.path.remove(helpers.RGOS)


class ProtectedTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rgos-fm-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.outside = tempfile.mkdtemp(prefix="rgos-fm-out-")
        self.addCleanup(shutil.rmtree, self.outside, True)
        for n in ("SYSTEM", "..SYSTEM", "games"):
            open(os.path.join(self.root, n), "w").close()
        self.fm = filemanager.FileManager([self.root], protected={self.root: ["SYSTEM", "..SYSTEM"]})

    def test_protected_names_and_things_inside_them(self):
        self.assertTrue(self.fm.is_protected(os.path.join(self.root, "SYSTEM")))
        self.assertTrue(self.fm.is_protected(os.path.join(self.root, "system", "x")))      # case-insensitive

    def test_protected_name_that_starts_with_two_dots(self):
        self.assertTrue(self.fm.is_protected(os.path.join(self.root, "..SYSTEM")))

    def test_unlisted_items_and_paths_outside_the_root_are_not_protected(self):
        self.assertFalse(self.fm.is_protected(os.path.join(self.root, "games")))
        self.assertFalse(self.fm.is_protected(self.root))
        self.assertFalse(self.fm.is_protected(os.path.join(self.outside, "SYSTEM")))

    def test_delete_keeps_a_protected_dotdot_name(self):
        self.fm.marked = {os.path.join(self.root, "..SYSTEM"), os.path.join(self.root, "games")}
        self.fm.delete().join()
        self.assertTrue(os.path.exists(os.path.join(self.root, "..SYSTEM")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "games")))


if __name__ == "__main__":
    unittest.main()
