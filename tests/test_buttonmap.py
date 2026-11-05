"""Settings > Map buttons wizard (menu.ButtonMap), including the first-boot setup, stick calibration and step retry.

The wizard talks to the input hub through two files in the run dir (keys.raw: last raw key press, sticks.raw: live stick
values). FakeHub writes them, and a fake clock replaces time.time() so every test runs instantly and deterministically.
"""
import json
import math
import os
import unittest
from unittest import mock

from tests import helpers

menu = helpers.load_menu()
BM = menu.ButtonMap
AXES = (0, 1, 3, 4)                    # ABS_X, ABS_Y, ABS_RX, ABS_RY
RANGE = (-32768, 32767)


class Clock:
    """Stands in for the `time` module inside menu.py (the wizard only calls time.time())."""
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class WizardCase(unittest.TestCase):
    def setUp(self):
        self.run_dir, self.btn_map = helpers.fresh_wizard_env(menu)
        self.clock = Clock()
        for p in (mock.patch.object(menu, "time", self.clock), mock.patch.object(BM, "GAP", 0.0)):
            p.start()
            self.addCleanup(p.stop)
        self.n = 0                                       # press counter (the hub numbers presses)
        self.rest = {a: 0 for a in AXES}                 # where the sticks rest
        self.over = {}                                   # axes currently held somewhere else
        self.has_sticks = False

    # ---- fake hub --------------------------------------------------------------------------------------------------
    def hub_sticks(self, present=True):
        """Switch the hub's analog output on/off (it only publishes sticks.raw on devices with sticks)."""
        self.has_sticks = present
        self.flush()

    def write_sticks(self, **over):
        """Hold the given axes (a0=..., a1=...) where told; everything else rests. No arguments = let go."""
        self.over = {int(k[1:]): v for k, v in over.items()}
        self.flush()

    def flush(self):
        """What the real hub does ~20 times a second while the wizard runs: publish the live stick values."""
        if not self.has_sticks:
            return
        vals = dict(self.rest)
        vals.update(self.over)
        path = os.path.join(self.run_dir, "sticks.raw")
        with open(path + ".tmp", "w") as f:
            json.dump({"ax": {str(a): [vals[a], RANGE[0], RANGE[1]] for a in AXES}}, f)
        os.replace(path + ".tmp", path)

    def press(self, wiz, code=0x130, name="pad"):
        self.n += 1
        with open(wiz.rawf, "w") as f:
            f.write("%d\t%s\t1\t%d\t0\n" % (self.n, name, code))
        self.clock.t += 0.6
        wiz.update()

    # ---- driving the wizard ----------------------------------------------------------------------------------------
    def start(self, sticks=False):
        self.hub_sticks(sticks)
        wiz = BM()
        update = wiz.update                              # the real hub keeps rewriting sticks.raw, so do the same before every update
        wiz.update = lambda: (self.flush(), update())[1]
        self.clock.t += 1.5
        wiz.update()
        self.assertEqual(wiz.state, "prompt")
        return wiz

    def tick(self, wiz, secs, step=0.05):
        end = self.clock.t + secs
        while self.clock.t < end and wiz.state not in ("done", "fail"):
            self.clock.t += step
            self.write_sticks()
            wiz.update()

    def run_buttons(self, wiz, skip=()):
        """Press every button prompt except the labels starting with a string in `skip` (those time out)."""
        code = 0x200
        while wiz.state == "prompt":
            label = wiz.PROMPTS[wiz.step][0]
            if label.startswith(tuple(skip)):
                self.clock.t += BM.OPT_TIMEOUT + 0.1
                wiz.update()
            else:
                code += 1
                self.press(wiz, code)

    def finish_saving(self, wiz):
        for _ in range(3):
            self.clock.t += 0.1
            wiz.update()

    def saved(self):
        with open(self.btn_map) as f:
            return json.load(f)

    def roll(self, wiz, axes, centre, rx, ry, seconds=2.0):
        """Move a stick round a circle of radius (rx, ry) around `centre`."""
        end, i = self.clock.t + seconds, 0
        while self.clock.t < end and wiz.state == "sroll":
            i += 1
            ph = i * 0.25
            self.clock.t += 0.02
            self.write_sticks(**{"a%d" % axes[0]: int(centre[0] + rx * math.cos(ph)),
                                 "a%d" % axes[1]: int(centre[1] + ry * math.sin(ph))})
            wiz.update()


class ButtonStepTests(WizardCase):
    def test_maps_every_button_and_saves_canonical_codes(self):
        wiz = self.start()
        self.run_buttons(wiz, skip=("MENU", "L2", "R2"))
        self.finish_saving(wiz)
        self.assertEqual(wiz.state, "done")
        keys = self.saved()["keys"]
        self.assertEqual(len(keys), 12)
        self.assertEqual(sorted(keys.values())[:2], [0x130, 0x131])           # B and A are among the canonical codes
        self.assertEqual(keys["pad|1:%d:0" % 0x201], 0x131)                   # the first press became A
        self.assertIn("MENU", wiz.msg)                                        # skipped optional buttons are reported
        self.assertNotIn("sticks", self.saved())

    def test_same_physical_button_cannot_be_used_twice(self):
        wiz = self.start()
        self.press(wiz, 0x300)                                                # A
        self.assertEqual(wiz.step, 1)
        self.press(wiz, 0x300)                                                # same key for B: ignored
        self.assertEqual(wiz.step, 1)
        self.press(wiz, 0x301)
        self.assertEqual(wiz.step, 2)

    def test_press_that_was_already_there_at_start_is_ignored(self):
        self.hub_sticks(False)
        os.makedirs(self.run_dir, exist_ok=True)
        wiz = BM()
        with open(wiz.rawf, "w") as f:                                        # e.g. the button that opened the wizard
            f.write("5\tpad\t1\t304\t0\n")
        self.clock.t += 1.5
        wiz.update()
        self.clock.t += 1.0
        wiz.update()
        self.assertEqual((wiz.state, wiz.step), ("prompt", 0))

    def test_optional_button_is_skipped_after_a_few_seconds(self):
        wiz = self.start()
        wiz.step = [p[0] for p in wiz.PROMPTS].index("MENU (home) button")
        wiz.tstate = self.clock.t
        self.clock.t += BM.OPT_TIMEOUT + 0.1
        wiz.update()
        self.assertEqual(wiz.skipped, ["MENU"])
        self.assertEqual(wiz.state, "prompt")

    def test_no_events_at_all_says_the_hub_is_not_running(self):
        wiz = self.start()
        self.clock.t += BM.TIMEOUT + 10
        wiz.update()
        self.assertEqual(wiz.state, "fail")
        self.assertIn("input hub", wiz.msg)

    def test_probe_flag_is_removed_when_finished(self):
        wiz = self.start()
        self.assertTrue(os.path.exists(wiz.flag))
        self.run_buttons(wiz, skip=("MENU", "L2", "R2"))
        self.finish_saving(wiz)
        self.assertFalse(os.path.exists(wiz.flag))


class RetryTests(WizardCase):
    def test_failed_step_is_retried_without_losing_progress(self):
        wiz = self.start()
        for i in range(5):                                                    # A B X Y L mapped
            self.press(wiz, 0x300 + i)
        self.clock.t += BM.TIMEOUT + 1                                        # R never pressed
        wiz.update()
        self.assertEqual(wiz.state, "fail")
        self.assertIn("R", wiz.msg)
        self.assertTrue(wiz.can_resume())
        self.assertFalse(os.path.exists(wiz.flag))                            # hub probe switched off while failed

        wiz.resume()
        self.assertTrue(os.path.exists(wiz.flag))
        self.clock.t += 1.5
        wiz.update()
        self.assertEqual((wiz.state, wiz.step), ("prompt", 5))                # asks R again, not A
        self.assertEqual(len(wiz.keys), 5)

        self.run_buttons(wiz, skip=("MENU", "L2", "R2"))
        self.finish_saving(wiz)
        self.assertEqual(wiz.state, "done")
        self.assertEqual(len(self.saved()["keys"]), 12)

    def test_failed_save_resumes_straight_into_apply(self):
        wiz = self.start()
        wiz.keys, wiz.state = {"pad|1:305:0": 0x131}, "apply"
        menu.BTN_MAP = "/proc/definitely/not/writable.json"
        wiz.update()
        self.assertEqual(wiz.state, "fail")
        self.assertTrue(wiz.can_resume())
        menu.BTN_MAP = self.btn_map
        wiz.resume()
        self.assertEqual(wiz.state, "apply")
        self.finish_saving(wiz)
        self.assertEqual(wiz.state, "done")
        self.assertEqual(self.saved()["keys"], {"pad|1:305:0": 0x131})

    def test_cannot_resume_when_not_failed(self):
        wiz = self.start()
        self.assertFalse(wiz.can_resume())
        wiz.resume()                                                          # harmless no-op
        self.assertEqual(wiz.state, "prompt")

    def test_failure_screen_explains_nothing_is_lost(self):
        wiz = self.start()
        self.clock.t += BM.TIMEOUT + 10
        wiz.update()
        self.assertTrue(any("only this step" in line for line in wiz.lines()))


class StickStepTests(WizardCase):
    def test_button_count_depends_on_whether_the_device_has_sticks(self):
        self.assertEqual(self.start(sticks=False).total(), 15)
        self.assertEqual(self.start(sticks=True).total(), 17)

    def test_device_without_sticks_never_asks_for_stick_clicks(self):
        wiz = self.start(sticks=False)
        seen = []
        code = 0x200
        while wiz.state == "prompt":
            label = wiz.PROMPTS[wiz.step][0]
            seen.append(label)
            if wiz.PROMPTS[wiz.step][2]:
                self.clock.t += BM.OPT_TIMEOUT + 0.1
                wiz.update()
            else:
                code += 1
                self.press(wiz, code)
        self.finish_saving(wiz)
        self.assertEqual(len(seen), 15)
        self.assertFalse(any(s.startswith(("L3", "R3")) for s in seen))
        self.assertEqual(wiz.state, "done")
        self.assertNotIn("sticks", self.saved())

    def test_device_with_sticks_asks_for_l3_and_r3(self):
        wiz = self.start(sticks=True)
        self.assertEqual(wiz.total(), 17)
        code, seen = 0x200, []
        while wiz.state == "prompt":
            seen.append(wiz.PROMPTS[wiz.step][0])
            if wiz.PROMPTS[wiz.step][1] in (0x138, 0x139, 0x13c):             # L2 / R2 / MENU not present
                self.clock.t += BM.OPT_TIMEOUT + 0.1
                wiz.update()
            else:
                code += 1
                self.press(wiz, code)
        self.assertEqual(len(seen), 17)
        self.assertEqual(wiz.state, "scenter")                                 # then the stick step starts
        # finish quickly: leave the sticks alone for both
        self.tick(wiz, BM.STICK_IDLE * 3 + 10)
        self.assertEqual(wiz.state, "done")
        values = list(self.saved()["keys"].values())
        self.assertIn(0x13d, values)
        self.assertIn(0x13e, values)

    def _to_sticks(self, rest=None):
        if rest:
            self.rest.update(rest)
        wiz = self.start(sticks=True)
        code = 0x200
        while wiz.state == "prompt":
            if wiz.PROMPTS[wiz.step][2]:
                self.clock.t += BM.OPT_TIMEOUT + 0.1
                wiz.update()
            else:
                code += 1
                self.press(wiz, code)
        self.assertEqual(wiz.state, "scenter")
        return wiz

    def test_calibration_measures_centre_and_travel_of_both_sticks(self):
        wiz = self._to_sticks({0: 120, 1: -90, 3: 40, 4: 60})                  # sticks rest slightly off centre
        self.tick(wiz, BM.CENTER_TIME + 0.5)
        self.assertEqual(wiz.state, "sroll")
        self.assertEqual(wiz.center, {0: 120, 1: -90, 3: 40, 4: 60})
        self.roll(wiz, (0, 1), (120, -90), 30000, 26000)
        self.tick(wiz, 4)                                                      # let go: no more growth, so the stick completes
        self.assertEqual((wiz.state, wiz.cstick), ("sroll", 1))
        self.roll(wiz, (3, 4), (40, 60), 28000, 28000)
        self.tick(wiz, 4)
        self.finish_saving(wiz)
        self.assertEqual(wiz.state, "done")
        self.assertIn("2 sticks", wiz.msg)
        cal = self.saved()["sticks"]
        self.assertEqual(set(cal), {"0", "1", "3", "4"})
        c, lo, hi = cal["0"]
        self.assertEqual(c, 120)
        self.assertAlmostEqual(lo, 120 - 30000, delta=800)
        self.assertAlmostEqual(hi, 120 + 30000, delta=800)
        c, lo, hi = cal["1"]
        self.assertEqual(c, -90)
        self.assertAlmostEqual(hi, -90 + 26000, delta=800)

    def test_stick_that_is_not_moved_is_skipped_not_failed(self):
        wiz = self._to_sticks()
        self.tick(wiz, BM.CENTER_TIME + 0.5)
        self.tick(wiz, BM.STICK_IDLE * 2 + 2)
        self.finish_saving(wiz)
        self.assertEqual(wiz.state, "done")
        self.assertIn("left stick", wiz.msg)
        self.assertIn("right stick", wiz.msg)
        self.assertNotIn("sticks", self.saved())

    def test_side_that_was_never_pushed_keeps_the_declared_range(self):
        wiz = self._to_sticks()
        self.tick(wiz, BM.CENTER_TIME + 0.5)
        end = self.clock.t + 1.0                                               # push right (+X) then down (+Y) only
        while self.clock.t < end:
            self.clock.t += 0.02
            frac = (self.clock.t - (end - 1.0))
            self.write_sticks(a0=int(20000 * min(frac * 2, 1)), a1=int(20000 * max(0, min((frac - 0.5) * 2, 1))))
            wiz.update()
        self.tick(wiz, 4)                                                      # holds still: 3 s without growth finishes it
        self.assertEqual((wiz.state, wiz.cstick), ("sroll", 1))
        c, lo, hi = wiz.cal[0]
        self.assertEqual(lo, RANGE[0])                                         # never pushed left: declared minimum
        self.assertAlmostEqual(hi, 20000, delta=400)                           # measured on the side that was used

    def test_stick_view_reports_position_and_reached_edges(self):
        wiz = self._to_sticks()
        self.tick(wiz, BM.CENTER_TIME + 0.5)
        name, x, y, reached = wiz.stick_view()
        self.assertEqual((round(x, 2), round(y, 2)), (0.0, 0.0))
        self.assertEqual(reached, set())
        self.write_sticks(a0=30000, a1=0)
        wiz.update()
        name, x, y, reached = wiz.stick_view()
        self.assertGreater(x, 0.9)
        self.assertIn((0, 1), reached)                                         # X axis, positive edge
        self.assertIsNone(BM().stick_view())                                   # no stick step running: nothing to draw

    def test_old_sticks_file_is_removed_when_a_wizard_starts(self):
        stale = os.path.join(self.run_dir, "sticks.raw")
        with open(stale, "w") as f:
            f.write("{}")
        BM()
        self.assertFalse(os.path.exists(stale))


if __name__ == "__main__":
    unittest.main()
