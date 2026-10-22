"""File manager (Tools > File Manager): browsing, copy/move/delete/rename and the safety rules. No pygame needed."""
import os, shutil, sys, tempfile, unittest

OPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay", "opt", "rgos")
sys.path.insert(0, OPT)
import filemanager as fmod


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class FM(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "roms")
        self.root2 = os.path.join(self.tmp, "roms2")
        for d in (self.root, self.root2):
            os.makedirs(d)
        touch(os.path.join(self.root, "gba", "Zelda.gba"), b"z" * 2048)
        touch(os.path.join(self.root, "gba", "Mario.gba"))
        touch(os.path.join(self.root, "gba", ".hidden"))
        touch(os.path.join(self.root, "nes", "a.nes"))
        self.fm = fmod.FileManager([self.root])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait(self, t):
        if t:
            t.join(5)

    def go(self, name):
        self.fm.idx = [e["name"] for e in self.fm.entries].index(name)
        self.fm.open_current()

    # ---- helpers
    def test_human_size(self):
        self.assertEqual(fmod.human_size(512), "512 B")
        self.assertEqual(fmod.human_size(1536), "1.5 KB")
        self.assertEqual(fmod.human_size(5 * 1048576), "5.0 MB")

    def test_valid_name(self):
        for bad in ("", "  ", ".", "..", "a/b", "a\\b", "x" * 200):
            self.assertFalse(fmod.valid_name(bad), bad)
        self.assertTrue(fmod.valid_name("Final Fantasy (USA).gba"))

    def test_inside_blocks_dotdot_and_symlinks(self):
        self.assertTrue(fmod.inside(os.path.join(self.root, "gba"), self.root))
        self.assertFalse(fmod.inside(os.path.join(self.root, "..", "roms2"), self.root))
        link = os.path.join(self.root, "escape")
        os.symlink("/", link)
        self.assertFalse(fmod.inside(link, self.root))

    def test_unique_name(self):
        self.assertEqual(fmod.unique_name(os.path.join(self.root, "gba"), "Zelda.gba"), "Zelda (copy).gba")
        self.assertEqual(fmod.unique_name(os.path.join(self.root, "gba"), "New.gba"), "New.gba")

    # ---- browsing
    def test_single_root_starts_inside_and_lists_folders_first(self):
        self.assertEqual(self.fm.cwd, self.root)
        self.assertEqual([e["name"] for e in self.fm.entries], ["gba", "nes"])
        self.go("gba")
        self.assertEqual([e["name"] for e in self.fm.entries], ["Mario.gba", "Zelda.gba"])   # hidden file skipped

    def test_go_up_returns_to_parent_with_cursor_on_the_folder(self):
        self.go("nes")
        self.assertTrue(self.fm.go_up())
        self.assertEqual(self.fm.current()["name"], "nes")
        self.assertFalse(self.fm.go_up())            # the only root: leave the file manager instead
        self.assertTrue(self.fm.at_top())

    def test_two_cards_start_on_the_storage_list(self):
        fm = fmod.FileManager([self.root, self.root2, "/nonexistent"])
        self.assertIsNone(fm.cwd)
        self.assertEqual(len(fm.entries), 2)
        fm.open_current()
        self.assertEqual(fm.cwd, self.root)
        self.assertTrue(fm.go_up())
        self.assertIsNone(fm.cwd)

    def test_cannot_enter_outside_the_roots(self):
        self.fm.enter("/etc")
        self.assertEqual(self.fm.cwd, self.root)

    # ---- operations
    def test_copy_and_paste_never_overwrites(self):
        self.go("gba")
        self.fm.idx = 1                                # Zelda.gba
        self.fm.copy()
        self.wait(self.fm.paste())                     # same folder -> "Zelda (copy).gba"
        names = [e["name"] for e in self.fm.entries]
        self.assertIn("Zelda (copy).gba", names)
        self.assertEqual(os.path.getsize(os.path.join(self.root, "gba", "Zelda (copy).gba")), 2048)
        self.assertEqual(os.path.getsize(os.path.join(self.root, "gba", "Zelda.gba")), 2048)

    def test_cut_and_paste_moves_between_folders(self):
        self.go("gba")
        self.fm.idx = 0                                # Mario.gba
        self.fm.cut()
        self.fm.go_up()
        self.go("nes")
        self.wait(self.fm.paste())
        self.assertTrue(os.path.exists(os.path.join(self.root, "nes", "Mario.gba")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "gba", "Mario.gba")))
        self.assertEqual(self.fm.clip, [])             # a move is used up

    def test_cannot_copy_or_move_a_folder_into_itself(self):
        d = os.path.join(self.root, "gba")
        with self.assertRaises(OSError):
            fmod.copy_item(d, d)
        with self.assertRaises(OSError):
            fmod.move_item(self.root, d)

    def test_failed_paste_reports_instead_of_raising(self):
        self.go("gba")
        self.fm.idx = 0
        self.fm.copy()
        shutil.rmtree(os.path.join(self.root, "nes"))
        self.fm.cwd = os.path.join(self.root, "nes")   # destination vanished
        self.wait(self.fm.paste())
        self.assertTrue(self.fm.msg.startswith("Failed"))
        self.assertEqual(self.fm.busy, "")

    def test_multi_mark_delete(self):
        self.go("gba")
        self.fm.idx = 0
        self.fm.toggle_mark()
        self.fm.toggle_mark()
        self.assertEqual(len(self.fm.marked), 2)
        self.wait(self.fm.delete())
        self.assertEqual(self.fm.entries, [])
        self.assertEqual(self.fm.msg, "Deleted 2 items")

    def test_delete_folder_recursively_but_never_a_root(self):
        self.fm.idx = 0                                # gba/
        self.wait(self.fm.delete())
        self.assertFalse(os.path.exists(os.path.join(self.root, "gba")))
        fm = fmod.FileManager([self.root, self.root2])
        self.assertEqual(fm.targets(), [])             # storage entries are not deletable
        self.assertIsNone(fm.delete())

    def test_rename(self):
        self.go("gba")
        self.fm.idx = 0
        self.assertFalse(self.fm.rename("a/b"))
        self.assertFalse(self.fm.rename("Zelda.gba"))  # already exists
        self.assertTrue(self.fm.rename("Mario Kart.gba"))
        self.assertEqual(self.fm.current()["name"], "Mario Kart.gba")
        self.assertTrue(os.path.exists(os.path.join(self.root, "gba", "Mario Kart.gba")))

    def test_mkdir(self):
        self.assertTrue(self.fm.mkdir("psx"))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "psx")))
        self.assertFalse(self.fm.mkdir("psx"))
        self.assertFalse(self.fm.mkdir(".."))

    def test_info(self):
        self.go("gba")
        self.fm.idx = 1
        self.assertIn("Size: 2.0 KB", self.fm.info())


class MenuWiring(unittest.TestCase):
    def test_menu_offers_the_tool_and_compiles(self):
        src = open(os.path.join(OPT, "menu.py")).read()
        compile(src, "menu.py", "exec")
        self.assertIn('"@view:files"', src)
        self.assertIn("import filemanager", src)


if __name__ == "__main__":
    unittest.main()
