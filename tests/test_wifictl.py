"""wifictl: a damaged or hand-edited wifi.json must not break Wi-Fi."""
import json
import os
import shutil
import sys
import tempfile
import unittest

from tests import helpers

sys.path.insert(0, helpers.RGOS)
try:
    import wifictl
finally:
    sys.path.remove(helpers.RGOS)


class LoadConfTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rgos-wifi-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        old = wifictl.CONF_FILE
        wifictl.CONF_FILE = os.path.join(self.dir, "wifi.json")
        self.addCleanup(setattr, wifictl, "CONF_FILE", old)

    def load(self, data):
        with open(wifictl.CONF_FILE, "w") as f:
            json.dump(data, f)
        return wifictl.load_conf()

    def test_non_numeric_top_level_prio_falls_back_to_zero(self):
        self.assertEqual(self.load({"prio": "x"})["prio"], 0)
        self.assertEqual(self.load({"prio": None})["prio"], 0)

    def test_networks_that_is_not_a_list_is_ignored(self):
        self.assertEqual(self.load({"networks": 5})["networks"], [])

    def test_bad_network_prio_does_not_break_the_wpa_config(self):
        conf = self.load({"networks": [{"ssid": "a", "prio": "hi"}, {"ssid": "b", "prio": 2}]})
        self.assertEqual([n["prio"] for n in conf["networks"]], [0, 2])
        text = wifictl.make_wpa_conf(conf)                      # used to raise TypeError when sorting
        self.assertIn("priority=2", text)

    def test_new_network_outranks_saved_ones(self):
        conf = self.load({"prio": 1, "networks": [{"ssid": "a", "psk": "", "prio": 7}]})
        self.assertEqual(conf["prio"], 7)
        self.assertTrue(wifictl.add_network("b"))
        new = {n["ssid"]: n["prio"] for n in wifictl.load_conf()["networks"]}
        self.assertGreater(new["b"], new["a"])


if __name__ == "__main__":
    unittest.main()
