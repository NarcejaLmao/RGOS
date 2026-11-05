"""rgos-input: stick calibration, the live stick feed for the wizard, and the button translation basics."""
import json
import os
import unittest
from unittest import mock

from tests import helpers

hub = helpers.load_hub()
FULL = (-32768, 32767)


def value_of(events):
    """The single EV_ABS value in a translator result."""
    assert len(events) == 1 and events[0][0] == hub.EV_ABS, events
    return events[0][2]


class StickScalingTests(unittest.TestCase):
    def tr(self, cal=None, analog=True):
        t = hub.Translator()
        t.analog = analog
        t.stick_cal = cal or {}
        return t

    def test_calibrated_centre_reads_zero_and_travel_reaches_full_scale(self):
        t = self.tr({0: (120.0, -20000.0, 30000.0)})
        self.assertEqual(value_of(t.stick(0, 120, FULL)), 0)
        self.assertEqual(value_of(t.stick(0, 30000, FULL)), hub.ASTICK)
        self.assertEqual(value_of(t.stick(0, -20000, FULL)), -hub.ASTICK)

    def test_each_side_is_scaled_by_its_own_travel(self):
        t = self.tr({0: (0.0, -10000.0, 30000.0)})
        self.assertGreater(value_of(t.stick(0, 15000, FULL)), 0)
        self.assertLess(value_of(t.stick(0, 15000, FULL)), 0.6 * hub.ASTICK)         # halfway out on the long side
        self.assertGreater(value_of(t.stick(0, -5000, FULL)), -0.6 * hub.ASTICK)     # 50% on the short side is about the same
        self.assertAlmostEqual(abs(value_of(t.stick(0, -5000, FULL))), value_of(t.stick(0, 15000, FULL)), delta=30)

    def test_values_beyond_the_measured_travel_are_clamped(self):
        t = self.tr({0: (0.0, -10000.0, 10000.0)})
        self.assertEqual(value_of(t.stick(0, 32000, FULL)), hub.ASTICK)
        self.assertEqual(value_of(t.stick(0, -32000, FULL)), -hub.ASTICK)

    def test_small_movements_inside_the_deadzone_read_zero(self):
        t = self.tr({0: (0.0, -10000.0, 10000.0)})
        small = int(10000 * hub.STICK_DEADZONE * 0.9)
        self.assertEqual(value_of(t.stick(0, small, FULL)), 0)
        self.assertEqual(value_of(t.stick(0, -small, FULL)), 0)
        just_out = int(10000 * (hub.STICK_DEADZONE + 0.05))
        self.assertGreater(value_of(t.stick(0, just_out, FULL)), 0)

    def test_output_is_continuous_across_the_deadzone_edge(self):
        t = self.tr({0: (0.0, -10000.0, 10000.0)})
        out = [value_of(t.stick(0, v, FULL)) for v in range(0, 10001, 50)]
        self.assertEqual(out, sorted(out))                                            # never goes backwards
        self.assertLess(max(b - a for a, b in zip(out, out[1:])), 400)                # and has no jump

    def test_uncalibrated_axis_uses_the_declared_range_as_before(self):
        t = self.tr({0: (0.0, -10000.0, 10000.0)})
        self.assertEqual(value_of(t.stick(3, 0, FULL)), 0)
        self.assertEqual(value_of(t.stick(3, 32767, FULL)), hub.ASTICK)
        self.assertEqual(value_of(t.stick(3, 255, (0, 255))), hub.ASTICK)             # an unsigned 0..255 axis still maps to the full range
        self.assertEqual(value_of(t.stick(3, 0, (0, 255))), -hub.ASTICK)

    def test_nothing_is_published_when_analog_is_off_or_for_other_axes(self):
        self.assertEqual(self.tr(analog=False).stick(0, 1000, FULL), [])
        self.assertEqual(self.tr().stick(0x10, 1000, FULL), [])                       # ABS_HAT0X is not a stick


class LoadSticksTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(helpers.sandbox(), "button-map.json")
        p = mock.patch.object(hub, "BTNMAP", self.path)
        p.start()
        self.addCleanup(p.stop)

    def write(self, data):
        with open(self.path, "w") as f:
            json.dump(data, f)

    def test_reads_centre_and_limits_per_axis(self):
        self.write({"keys": {}, "sticks": {"0": [120, -20000, 30000], "4": [60.5, -1000, 2000]}})
        self.assertEqual(hub.load_sticks(), {0: (120.0, -20000.0, 30000.0), 4: (60.5, -1000.0, 2000.0)})

    def test_missing_or_broken_file_means_no_calibration(self):
        self.assertEqual(hub.load_sticks(), {})
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(hub.load_sticks(), {})
        self.write({"keys": {}})
        self.assertEqual(hub.load_sticks(), {})

    def test_unusable_entries_are_ignored(self):
        self.write({"sticks": {"0": [5, 10, 20],          # centre outside its limits
                               "1": "x",                  # not numbers
                               "3": [0, -1, 1],           # travel too small to trust
                               "9": [0, -100, 100],       # not a stick axis
                               "4": [0, -5000, 5000]}})   # fine
        self.assertEqual(hub.load_sticks(), {4: (0.0, -5000.0, 5000.0)})


class FakeSource:
    def __init__(self, axes):
        self.axes = axes

    def absinfo(self, c):
        return self.axes.get(c)


class StickFeedTests(unittest.TestCase):
    """KeyProbe.sticks(): what the wizard sees while it runs."""
    def setUp(self):
        d = helpers.sandbox()
        self.flag, self.out = os.path.join(d, "keys.probe"), os.path.join(d, "sticks.raw")
        for name, val in (("KEYS_PROBE", self.flag), ("STICKS_RAW", self.out)):
            p = mock.patch.object(hub, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.probe = hub.KeyProbe()

    def feed(self, srcs):
        self.probe.next_stick = self.probe.next_check = 0.0
        self.probe.sticks(srcs)

    def test_writes_live_values_and_ranges_while_the_wizard_runs(self):
        open(self.flag, "w").close()
        self.feed({"a": FakeSource({0: (100, -32768, 32767), 1: (-50, -32768, 32767), 3: (7, 0, 255)})})
        with open(self.out) as f:
            ax = json.load(f)["ax"]
        self.assertEqual(ax, {"0": [100, -32768, 32767], "1": [-50, -32768, 32767], "3": [7, 0, 255]})   # axis 4 absent: not listed

    def test_writes_nothing_when_the_wizard_is_not_running(self):
        self.feed({"a": FakeSource({0: (1, -10, 10)})})
        self.assertFalse(os.path.exists(self.out))

    def test_values_are_raw_even_when_a_calibration_exists(self):
        open(self.flag, "w").close()
        self.feed({"a": FakeSource({0: (5000, -32768, 32767)})})
        with open(self.out) as f:
            self.assertEqual(json.load(f)["ax"]["0"][0], 5000)

    def test_uses_the_first_source_that_has_the_axis(self):
        open(self.flag, "w").close()
        self.feed({"a": FakeSource({}), "b": FakeSource({0: (9, -10, 10)})})
        with open(self.out) as f:
            self.assertEqual(json.load(f)["ax"]["0"][0], 9)

    def test_is_rate_limited(self):
        open(self.flag, "w").close()
        self.feed({"a": FakeSource({0: (1, -10, 10)})})
        os.unlink(self.out)
        self.probe.sticks({"a": FakeSource({0: (2, -10, 10)})})                       # immediately again: skipped
        self.assertFalse(os.path.exists(self.out))


class ButtonTranslationTests(unittest.TestCase):
    def test_hat_axes_become_the_dpad(self):
        t = hub.Translator()
        self.assertEqual(t.abs("pad", 0x10, 32767, FULL), [(hub.EV_KEY, 0x223, 1)])   # right
        self.assertEqual(t.abs("pad", 0x10, 0, FULL), [(hub.EV_KEY, 0x223, 0)])
        self.assertEqual(t.abs("pad", 0x11, -32768, FULL), [(hub.EV_KEY, 0x220, 1)])  # up

    def test_stick_axes_do_not_press_buttons(self):
        t = hub.Translator()
        self.assertEqual(t.abs("pad", 0x00, 32767, FULL), [])

    def test_wizard_mapping_wins_and_ab_swap_applies_on_top(self):
        t = hub.Translator({("pad", 1, 300, 0): 0x131})
        self.assertEqual(t.key("pad", 300, 1), [(hub.EV_KEY, 0x131, 1)])
        t.swap = {0x130: 0x131, 0x131: 0x130}
        self.assertEqual(t.key("pad", 300, 1), [(hub.EV_KEY, 0x130, 1)])

    def test_common_alternative_codes_are_normalised(self):
        t = hub.Translator()
        self.assertEqual(t.key("pad", 0x121, 1), [(hub.EV_KEY, 0x131, 1)])            # BTN_THUMB -> A
        self.assertEqual(t.key("pad", 0x12b, 1), [(hub.EV_KEY, 0x13d, 1)])            # BTN_BASE6 -> L3

    def test_unknown_keys_are_dropped(self):
        self.assertEqual(hub.Translator().key("pad", 0x2ff, 1), [])


if __name__ == "__main__":
    unittest.main()
