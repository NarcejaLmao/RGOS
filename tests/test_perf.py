"""rgos-launch: in-game performance helpers (governor, verbose log toggle, game flag) against a fake sysfs."""
import os
import unittest
from unittest import mock

from tests import helpers
from tests.test_launch import load_launch


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


class GovernorTests(unittest.TestCase):
    def setUp(self):
        self.launch = load_launch()
        self.root = helpers.sandbox()
        self.boot = helpers.sandbox()
        self.launch.SYS_ROOT = self.root
        self.launch.BOOT_DIR = self.boot
        self.cpu0 = os.path.join(self.root, "devices/system/cpu/cpu0/cpufreq")
        self.cpu1 = os.path.join(self.root, "devices/system/cpu/cpu1/cpufreq")
        self.gpu = os.path.join(self.root, "class/devfreq/gpu")
        for d in (self.cpu0, self.cpu1):
            write(d + "/scaling_governor", "schedutil")
            write(d + "/scaling_available_governors", "schedutil performance powersave")
        write(self.gpu + "/governor", "simple_ondemand")
        write(self.gpu + "/available_governors", "simple_ondemand performance powersave")

    def test_heavy_systems_get_performance_light_ones_do_not(self):
        for sid in ("psx", "gc", "n64", "dreamcast", "saturn", "psp"):
            self.assertEqual(self.launch.perf_governor(sid), "performance", sid)
        for sid in ("gb", "gba", "nes", "snes", "genesis", ""):
            self.assertEqual(self.launch.perf_governor(sid), "", sid)

    def test_boot_file_overrides_for_every_system(self):
        write(os.path.join(self.boot, "rgos-governor.txt"), "performance\n")
        self.assertEqual(self.launch.perf_governor("gba"), "performance")
        write(os.path.join(self.boot, "rgos-governor.txt"), "default\n")
        self.assertEqual(self.launch.perf_governor("psx"), "")
        write(os.path.join(self.boot, "rgos-governor.txt"), "../../etc\n")   # junk is ignored, the per-system default applies
        self.assertEqual(self.launch.perf_governor("psx"), "performance")

    def test_set_and_restore_cpu_and_gpu(self):
        saved = self.launch.set_governor("performance")
        self.assertEqual(read(self.cpu0 + "/scaling_governor"), "performance")
        self.assertEqual(read(self.cpu1 + "/scaling_governor"), "performance")
        self.assertEqual(read(self.gpu + "/governor"), "performance")
        self.launch.restore_governor(saved)
        self.assertEqual(read(self.cpu0 + "/scaling_governor"), "schedutil")
        self.assertEqual(read(self.cpu1 + "/scaling_governor"), "schedutil")
        self.assertEqual(read(self.gpu + "/governor"), "simple_ondemand")

    def test_node_without_the_governor_is_left_alone(self):
        write(self.gpu + "/available_governors", "simple_ondemand")
        saved = self.launch.set_governor("performance")
        self.assertNotIn(self.gpu + "/governor", saved)
        self.assertEqual(read(self.gpu + "/governor"), "simple_ondemand")

    def test_empty_governor_touches_nothing(self):
        self.assertEqual(self.launch.set_governor(""), {})
        self.assertEqual(read(self.cpu0 + "/scaling_governor"), "schedutil")


class SwitchesTests(unittest.TestCase):
    def setUp(self):
        self.launch = load_launch()
        self.launch.BOOT_DIR = helpers.sandbox()
        self.launch.RUN_DIR = helpers.sandbox()
        self.launch.GAME_FLAG = os.path.join(self.launch.RUN_DIR, "ingame")

    def test_verbose_is_off_by_default_and_opt_in(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RGOS_RA_VERBOSE", None)
            self.assertFalse(self.launch.ra_verbose())
            cmd = self.launch.build_command("/c.so", "/r.gba", "/roms", "/run.cfg")
            self.assertNotIn("--verbose", cmd)
            write(os.path.join(self.launch.BOOT_DIR, "retroarch-verbose.txt"), "")
            self.assertTrue(self.launch.ra_verbose())
            self.assertIn("--verbose", self.launch.build_command("/c.so", "/r.gba", "/roms", "/run.cfg"))

    def test_game_flag_on_and_off(self):
        self.launch.set_game_flag(True)
        self.assertTrue(os.path.exists(self.launch.GAME_FLAG))
        self.launch.set_game_flag(False)
        self.assertFalse(os.path.exists(self.launch.GAME_FLAG))
        self.launch.set_game_flag(False)               # removing twice must not raise

    def test_unload_menu_drops_menu_and_pygame(self):
        import sys
        for n in ("menu", "pygame", "pygame.mixer"):
            sys.modules[n] = mock.MagicMock()
        self.launch.unload_menu()
        for n in ("menu", "pygame", "pygame.mixer"):
            self.assertNotIn(n, sys.modules)


if __name__ == "__main__":
    unittest.main()
