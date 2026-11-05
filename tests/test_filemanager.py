"""filemanager: the protected boot files stay protected, whatever the name looks like."""
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

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


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rgos-fm-")
        self.addCleanup(shutil.rmtree, self.root, True)
        for n in ("mario.nes", "zelda.nes"):
            open(os.path.join(self.root, n), "w").close()
        self.fm = filemanager.FileManager([self.root])

    def select(self, name):
        self.fm.idx = [e["name"] for e in self.fm.entries].index(name)

    def test_rename_onto_another_existing_file_is_refused(self):
        self.select("mario.nes")
        self.assertFalse(self.fm.rename("zelda.nes"))
        self.assertTrue(os.path.exists(os.path.join(self.root, "mario.nes")))

    def test_case_only_rename_is_not_reported_as_existing_on_a_case_insensitive_fs(self):
        self.select("mario.nes")
        src = os.path.join(self.root, "mario.nes")
        with unittest.mock.patch.object(filemanager.os.path, "lexists", return_value=True), \
             unittest.mock.patch.object(filemanager, "same_entry", return_value=True):
            self.assertTrue(self.fm.rename("Mario.nes"))
        self.assertIn("Mario.nes", os.listdir(self.root))
        self.assertFalse(os.path.exists(src) and "mario.nes" in os.listdir(self.root))

    def test_same_entry(self):
        a = os.path.join(self.root, "mario.nes")
        self.assertTrue(filemanager.same_entry(a, a))
        self.assertFalse(filemanager.same_entry(a, os.path.join(self.root, "zelda.nes")))
        self.assertFalse(filemanager.same_entry(a, os.path.join(self.root, "missing")))


if __name__ == "__main__":
    unittest.main()
