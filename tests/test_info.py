"""Settings > Information: the text helpers and the list of rows (the page scrolls, so the list may be longer than the screen)."""
import unittest
from unittest import mock

from tests import helpers

menu = helpers.load_menu()


def files(mapping):
    """A replacement for menu.rd_file that serves fake /proc and /sys files."""
    return lambda path: mapping.get(path, "")


class HelperTests(unittest.TestCase):
    def test_uptime(self):
        with mock.patch.object(menu, "rd_file", files({"/proc/uptime": "93784.12 1000.0\n"})):
            self.assertEqual(menu._uptime_text(), "1d 2h 03m")
        with mock.patch.object(menu, "rd_file", files({"/proc/uptime": "125.0 0\n"})):
            self.assertEqual(menu._uptime_text(), "0h 02m")
        with mock.patch.object(menu, "rd_file", files({})):
            self.assertEqual(menu._uptime_text(), "n/a")

    def test_memory(self):
        meminfo = "MemTotal:        2048000 kB\nMemFree:          100000 kB\nMemAvailable:    1024000 kB\n"
        with mock.patch.object(menu, "rd_file", files({"/proc/meminfo": meminfo})):
            self.assertEqual(menu._mem_text(), "1000 / 2000 MB free")
        with mock.patch.object(menu, "rd_file", files({"/proc/meminfo": "MemTotal: 1024000 kB\nMemFree: 512000 kB\n"})):
            self.assertEqual(menu._mem_text(), "500 / 1000 MB free")           # older kernels have no MemAvailable
        with mock.patch.object(menu, "rd_file", files({})):
            self.assertEqual(menu._mem_text(), "n/a")

    def test_cpu(self):
        with mock.patch.object(menu, "rd_file", files({"/proc/cpuinfo": "processor\t: 0\nmodel name\t: Cortex-A53\n"})), \
                mock.patch.object(menu.os, "cpu_count", return_value=4):
            self.assertEqual(menu._cpu_text(), "Cortex-A53 x4")
        with mock.patch.object(menu, "rd_file", files({})), mock.patch.object(menu.os, "cpu_count", return_value=1):
            self.assertEqual(menu._cpu_text(), "1 core")

    def test_temperature_uses_the_first_readable_zone(self):
        zones = ["/sys/class/thermal/thermal_zone0/temp", "/sys/class/thermal/thermal_zone1/temp"]
        with mock.patch.object(menu.glob, "glob", return_value=zones), \
                mock.patch.object(menu, "rd_file", files({zones[0]: "garbage", zones[1]: "48500\n"})):
            self.assertEqual(menu._temp_text(), "48 C")                         # 48.5 rounds to even; the point is the zone fallback
        with mock.patch.object(menu.glob, "glob", return_value=[]):
            self.assertEqual(menu._temp_text(), "n/a")

    def test_load_average(self):
        with mock.patch.object(menu, "rd_file", files({"/proc/loadavg": "0.52 0.40 0.31 1/120 999\n"})):
            self.assertEqual(menu._load_text(), "0.52 0.40 0.31")
        with mock.patch.object(menu, "rd_file", files({})):
            self.assertEqual(menu._load_text(), "n/a")

    def test_root_filesystem_space(self):
        st = mock.Mock(f_bavail=1000, f_frsize=1_000_000, f_blocks=4000)
        with mock.patch.object(menu.os, "statvfs", return_value=st):
            self.assertEqual(menu._rootfs_text(), "1.0 / 4.0 GB free")
        with mock.patch.object(menu.os, "statvfs", side_effect=OSError):
            self.assertEqual(menu._rootfs_text(), "n/a")


class SettingsInfoTests(unittest.TestCase):
    def rows(self):
        with mock.patch.object(menu, "sdcard", None), \
                mock.patch.object(menu, "read_volume", return_value=None), \
                mock.patch.object(menu, "read_battery", return_value=None), \
                mock.patch.object(menu, "read_brightness", return_value=None), \
                mock.patch.object(menu, "swap_ab_on", return_value=True), \
                mock.patch.object(menu, "rd_file", files({})):
            return menu.settings_info()

    def test_rows_are_label_value_text_pairs(self):
        rows = self.rows()
        for label, value in rows:
            self.assertIsInstance(label, str)
            self.assertIsInstance(value, str)
            self.assertTrue(label and value, (label, value))

    def test_labels_are_unique_and_include_the_new_system_rows(self):
        labels = [l for l, _ in self.rows()]
        self.assertEqual(len(labels), len(set(labels)))
        for want in ("RG OS build", "Kernel", "Battery", "A/B buttons", "CPU", "CPU temp", "Load average",
                     "Memory", "System storage", "Uptime", "Resolution"):
            self.assertIn(want, labels)

    def test_there_are_more_rows_than_one_screen_shows(self):
        # the page is only worth scrolling if the list can outgrow the panel (about 7 rows on the smallest screen)
        self.assertGreater(len(self.rows()), 10)

    def test_missing_hardware_reads_n_a_instead_of_failing(self):
        values = dict(self.rows())
        self.assertEqual(values["Battery"], "n/a")
        self.assertEqual(values["Volume"], "n/a")
        self.assertEqual(values["A/B buttons"], "swapped")


if __name__ == "__main__":
    unittest.main()
