"""Alternative libretro cores: offered in Settings > Emulators for the right systems, and installed by build.sh. No pygame needed."""
import ast, os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def compat_table():
    src = open(os.path.join(ROOT, "overlay", "opt", "rgos", "menu.py")).read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SYSTEM_CORE_COMPATIBILITY":
            return ast.literal_eval(node.value)
    raise AssertionError("SYSTEM_CORE_COMPATIBILITY not found")


class AlternativeCoresTest(unittest.TestCase):
    WANT = {"gba": "gpsp", "snes": "snes9x2010", "nes": "nestopia", "genesis": "picodrive", "segacd": "picodrive",
            "sega32x": "picodrive", "mastersystem": "picodrive", "gamegear": "picodrive"}

    def test_menu_offers_each_alternative(self):
        t = compat_table()
        for sid, core in self.WANT.items():
            self.assertIn(core, t[sid], sid)

    def test_default_core_stays_first(self):                  # the picker's preferred order: default first
        t = compat_table()
        for sid, first in {"gba": "mgba", "snes": "snes9x", "nes": "fceumm", "genesis": "genesis_plus_gx"}.items():
            self.assertEqual(t[sid][0], first, sid)

    def test_build_installs_the_cores(self):
        b = open(os.path.join(ROOT, "build.sh")).read()
        m = re.search(r"for CORE in ([a-z0-9_ ]+); do", b)
        self.assertIsNotNone(m)
        self.assertEqual(set(m.group(1).split()), {"gpsp", "snes9x2010", "nestopia", "picodrive"})

    def test_every_installed_alternative_is_selectable(self):
        t = compat_table()
        b = open(os.path.join(ROOT, "build.sh")).read()
        for core in re.search(r"for CORE in ([a-z0-9_ ]+); do", b).group(1).split():
            self.assertTrue(any(core in v for v in t.values()), core)


class FbneoTest(unittest.TestCase):
    def read(self, *p):
        return open(os.path.join(ROOT, *p)).read()

    def test_systems_are_defined(self):
        src = self.read("overlay", "opt", "rgos", "menu.py")
        for sid, tag in (("arcade", "ARC"), ("neogeo", "NEO")):
            self.assertRegex(src, r'\("%s", "%s", "retroarch", \("\.zip", "\.7z"\)' % (sid, tag))
            self.assertIn('"%s": "fbneo"' % sid, src)
        t = compat_table()
        self.assertEqual((t["arcade"], t["neogeo"]), (("fbneo",), ("fbneo",)))

    def test_session_launches_and_creates_folders(self):
        s = self.read("overlay", "opt", "rgos", "rgos-session")
        for sid in ("arcade", "neogeo"):
            self.assertIn("*/roms/%s/*) core=$(configured_core %s fbneo)" % (sid, sid), s)
            for kind in ("roms", "saves", "bios"):
                self.assertIn("/media/roms/%s/%s" % (kind, sid), s)

    def test_second_card_knows_the_folders(self):
        s = self.read("overlay", "opt", "rgos", "sdcard.py")
        self.assertIn('"arcade", "neogeo"', s)

    def test_build_requires_the_core(self):
        b = self.read("build.sh")
        self.assertIn("fbneo_libretro.so.zip", b)
        self.assertIn("Failed to download the official ARM64 FBNeo core", b)       # required core: build stops without it


class AtariTest(unittest.TestCase):
    SYSTEMS = {"atari2600": ("A26", "stella2014"), "atari5200": ("A52", "atari800"), "atari7800": ("A78", "prosystem")}

    def read(self, *p):
        return open(os.path.join(ROOT, *p)).read()

    def test_menu_defines_the_systems(self):
        src, t = self.read("overlay", "opt", "rgos", "menu.py"), compat_table()
        for sid, (tag, core) in self.SYSTEMS.items():
            self.assertRegex(src, r'\("%s", "%s", "retroarch"' % (sid, tag))
            self.assertIn('"%s": "%s"' % (sid, core), src)
            self.assertEqual(t[sid][0], core)

    def test_session_launches_and_creates_folders(self):
        s = self.read("overlay", "opt", "rgos", "rgos-session")
        for sid, (tag, core) in self.SYSTEMS.items():
            self.assertIn("*/roms/%s/*)" % sid, s)
            self.assertIn("configured_core %s %s" % (sid, core), s)
            for kind in ("roms", "saves", "bios"):
                self.assertIn("/media/roms/%s/%s" % (kind, sid), s)

    def test_5200_needs_its_bios(self):
        s = self.read("overlay", "opt", "rgos", "rgos-session")
        self.assertIn("/media/roms/bios/atari5200/5200.rom", s)

    def test_second_card_and_build(self):
        self.assertIn('"atari2600", "atari5200", "atari7800"', self.read("overlay", "opt", "rgos", "sdcard.py"))
        self.assertIn("for CORE in stella2014 atari800 prosystem; do", self.read("build.sh"))


class NgpTest(unittest.TestCase):
    def read(self, *p):
        return open(os.path.join(ROOT, *p)).read()

    def test_menu_and_core_list(self):
        src = self.read("overlay", "opt", "rgos", "menu.py")
        self.assertRegex(src, r'\("ngp", "NGP", "retroarch", \("\.ngp", "\.ngc", "\.ngpc"\)')
        self.assertIn('"ngp": "mednafen_ngp"', src)
        self.assertEqual(compat_table()["ngp"], ("mednafen_ngp", "race"))

    def test_session_folders_and_launch(self):
        s = self.read("overlay", "opt", "rgos", "rgos-session")
        self.assertIn("*/roms/ngp/*) core=$(configured_core ngp mednafen_ngp)", s)
        for kind in ("roms", "saves", "bios"):
            self.assertIn("/media/roms/%s/ngp" % kind, s)

    def test_second_card_and_build(self):
        self.assertIn('"ngp"', self.read("overlay", "opt", "rgos", "sdcard.py"))
        self.assertIn("for CORE in mednafen_ngp race; do", self.read("build.sh"))


class WonderSwanTest(unittest.TestCase):
    def read(self, *p):
        return open(os.path.join(ROOT, *p)).read()

    def test_menu_and_core_list(self):
        src = self.read("overlay", "opt", "rgos", "menu.py")
        self.assertRegex(src, r'\("wonderswan", "WS", "retroarch", \("\.ws", "\.wsc", "\.pc2"\)')
        self.assertIn('"wonderswan": "mednafen_wswan"', src)
        self.assertEqual(compat_table()["wonderswan"], ("mednafen_wswan",))

    def test_session_folders_and_launch(self):
        s = self.read("overlay", "opt", "rgos", "rgos-session")
        self.assertIn("*/roms/wonderswan/*) core=$(configured_core wonderswan mednafen_wswan)", s)
        for kind in ("roms", "saves", "bios"):
            self.assertIn("/media/roms/%s/wonderswan" % kind, s)

    def test_second_card_and_build(self):
        self.assertIn('"wonderswan"', self.read("overlay", "opt", "rgos", "sdcard.py"))
        self.assertIn("CORE=mednafen_wswan", self.read("build.sh"))


class DreamcastTest(unittest.TestCase):
    def read(self, *p):
        return open(os.path.join(ROOT, *p)).read()

    def test_menu_and_core_list(self):
        src = self.read("overlay", "opt", "rgos", "menu.py")
        self.assertRegex(src, r'\("dreamcast", "DC", "retroarch", \("\.cdi", "\.gdi", "\.chd", "\.cue", "\.m3u"\)')
        self.assertIn('"dreamcast": "flycast"', src)
        self.assertEqual(compat_table()["dreamcast"], ("flycast",))

    def test_session_folders_and_launch(self):
        s = self.read("overlay", "opt", "rgos", "rgos-session")
        self.assertIn("configured_core dreamcast flycast", s)
        for kind in ("roms", "saves", "bios"):
            self.assertIn("/media/roms/%s/dreamcast" % kind, s)

    def test_second_card_and_build(self):
        self.assertIn('"dreamcast"', self.read("overlay", "opt", "rgos", "sdcard.py"))
        self.assertIn("CORE=flycast", self.read("build.sh"))


if __name__ == "__main__":
    unittest.main()
