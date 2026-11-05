"""Menu navigation with the left stick: the stick acts like the D-pad (hold, auto-repeat, hysteresis)."""
import unittest
from unittest import mock

from tests import helpers

menu = helpers.load_menu()
FULL = 32767
ABS_X, ABS_Y = 0x00, 0x01


class FakeEv:
    def __init__(self):
        self.queue = []

    def read(self):
        out, self.queue = self.queue, []
        return out


class StickNavTests(unittest.TestCase):
    def setUp(self):
        for name, val in (("EV_KEY", 1), ("EV_ABS", 3), ("EV_CODES", {})):   # evpad is mocked in the tests
            p = mock.patch.object(menu, name, val)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(menu.os.path, "exists", return_value=False)    # screen is on
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(menu.pygame.event, "get", return_value=[])
        p.start()
        self.addCleanup(p.stop)
        self.inp = menu.Input()
        self.inp.ev = FakeEv()

    def move(self, axis, frac):
        self.inp.ev.queue.append((3, axis, int(frac * FULL)))
        return self.inp.poll()

    def test_each_direction_gives_one_action(self):
        for axis, frac, want in ((ABS_X, -1, "left"), (ABS_X, 1, "right"), (ABS_Y, -1, "up"), (ABS_Y, 1, "down")):
            with self.subTest(want=want):
                self.assertEqual(self.move(axis, frac), [want])
                self.assertEqual(self.move(axis, 0), [])
                self.assertEqual(self.inp.held, set())

    def test_small_movement_inside_the_threshold_does_nothing(self):
        self.assertEqual(self.move(ABS_X, 0.3), [])
        self.assertEqual(self.move(ABS_Y, -0.5), [])

    def test_holding_the_stick_does_not_repeat_the_press_until_the_delay(self):
        self.assertEqual(self.move(ABS_Y, 1), ["down"])
        self.assertEqual(self.inp.poll(), [])                  # still held, repeat not due yet
        self.assertIn("down", self.inp.repeat)

    def test_hold_repeats_like_the_dpad(self):
        self.move(ABS_Y, 1)
        self.inp.repeat["down"] = 0                            # delay has passed
        self.assertEqual(self.inp.poll(), ["down"])

    def test_hysteresis_keeps_direction_until_below_release_level(self):
        self.assertEqual(self.move(ABS_X, 1), ["right"])
        self.assertEqual(self.move(ABS_X, 0.45), [])           # between OFF and ON: still held, no new press
        self.assertEqual(self.inp.held, {"right"})
        self.assertEqual(self.move(ABS_X, 0.2), [])
        self.assertEqual(self.inp.held, set())

    def test_dominant_axis_wins_and_switching_releases_the_old_direction(self):
        self.assertEqual(self.move(ABS_X, 1), ["right"])
        self.assertEqual(self.move(ABS_Y, -1), [])             # equal pull: x still dominant (>=)
        self.assertEqual(self.move(ABS_X, 0.4), ["up"])
        self.assertEqual(self.inp.held, {"up"})
        self.assertNotIn("right", self.inp.repeat)

    def test_dpad_still_works_alongside(self):
        self.inp.ev.queue.append((3, 0x11, 1))
        self.assertEqual(self.inp.poll(), ["down"])

    def test_screen_off_clears_the_stick(self):
        self.move(ABS_X, 1)
        with mock.patch.object(menu.os.path, "exists", return_value=True):
            self.inp.poll()
        self.assertEqual((self.inp.held, self.inp.stick_dir), (set(), None))
        self.assertEqual(self.move(ABS_X, 1), ["right"])       # and it works again afterwards


if __name__ == "__main__":
    unittest.main()
