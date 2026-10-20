"""Forced first-run calibration: when it is required, and when an existing install is let through (pygame stubbed)."""
import os, sys, tempfile, unittest
from unittest import mock

OPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay", "opt", "rgos")
sys.path.insert(0, OPT)


def load(device, d):
    sys.modules.pop("menu", None)
    env = {"RGOS_DEVICE": device, "RGOS_CALIBDONE": d + "/calibrated", "RGOS_STICKMAP": d + "/stick-map.json",
           "RGOS_BTNMAP": d + "/button-map.json"}
    with mock.patch.dict(os.environ, env), mock.patch.dict(sys.modules, {"pygame": mock.MagicMock()}):
        import menu
        return menu


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.d = self.t.name
    def tearDown(self): self.t.cleanup()
    def touch(self, n): open(os.path.join(self.d, n), "w").close()

    def test_fresh_install_needs_setup(self):
        self.assertTrue(load("rg34xx-sp", self.d).calibration_needed())

    def test_marker_ends_it(self):
        m = load("rg34xx-sp", self.d)
        m.mark_calibrated()
        self.assertTrue(os.path.exists(self.d + "/calibrated"))
        self.assertFalse(m.calibration_needed())

    def test_half_done_still_needed(self):
        self.touch("stick-map.json")                       # sticks done, buttons not
        self.assertTrue(load("rg34xx-sp", self.d).calibration_needed())

    def test_existing_calibration_is_grandfathered(self):
        self.touch("stick-map.json"); self.touch("button-map.json")
        self.assertFalse(load("rg34xx-sp", self.d).calibration_needed())
        self.assertTrue(os.path.exists(self.d + "/calibrated"))


if __name__ == "__main__":
    unittest.main()
