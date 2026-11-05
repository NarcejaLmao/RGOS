"""hwctl.Motor: GPIO selection."""
import json
import os
import sys
import unittest

from tests import helpers

sys.path.insert(0, helpers.RGOS)
import hwctl  # noqa: E402


def fake_gpio(n):
    root = helpers.sandbox()
    gpio_root = os.path.join(root, "gpio")
    os.makedirs(os.path.join(gpio_root, "gpio%d" % n))
    open(os.path.join(gpio_root, "gpio%d" % n, "value"), "w").close()
    return root, gpio_root


class MotorGpioTests(unittest.TestCase):
    def test_gpio_zero_is_a_valid_pin(self):
        root, gpio_root = fake_gpio(0)
        m = hwctl.Motor({"rumble": {"gpio": 0}}, sys_root=root, gpio_root=gpio_root)
        self.assertEqual((m.mode, m.path), ("gpio", 0))

    def test_unconfigured_gpio_is_ignored(self):
        root, gpio_root = fake_gpio(0)
        for val in (None, "", False):
            m = hwctl.Motor({"rumble": {"gpio": val}}, sys_root=root, gpio_root=gpio_root)
            self.assertEqual(m.mode, "none")


class LoadConfTests(unittest.TestCase):
    def write(self, name, data):
        p = os.path.join(helpers.sandbox(), name)
        with open(p, "w") as f:
            json.dump(data, f)
        return p

    def test_shipped_config_that_is_not_an_object_gives_none(self):
        user = self.write("hw.json", {"audio": {"card": 1}})
        for bad in ([1, 2], "x", 5, None):              # used to raise TypeError when a user hw.json existed
            self.assertIsNone(hwctl.load_conf(self.write("hw-x.json", bad), user), bad)

    def test_user_overrides_are_merged(self):
        shipped = self.write("hw-x.json", {"audio": {"card": 0, "step": 5}})
        user = self.write("hw.json", {"audio": {"card": 1}})
        self.assertEqual(hwctl.load_conf(shipped, user), {"audio": {"card": 1, "step": 5}})


class AudioInitLinesTests(unittest.TestCase):
    def lines(self, shipped_text, user):
        d = helpers.sandbox()
        shipped = os.path.join(d, "init.txt")
        with open(shipped, "w") as f:
            f.write(shipped_text)
        u = os.path.join(d, "hw.json")
        with open(u, "w") as f:
            json.dump(user, f)
        return hwctl._audio_init_lines(shipped, u)

    def test_shipped_and_user_lines_are_split_like_a_shell(self):
        got = self.lines("# comment\n\nsset 'Speaker Switch' on\n", {"audio": {"init": ["cset name='Speaker Mux' 1", 5, None]}})
        self.assertEqual(got, [["sset", "Speaker Switch", "on"], ["cset", "name=Speaker Mux", "1"]])

    def test_init_that_is_not_a_list_is_ignored(self):
        for bad in ("sset 'Speaker Switch' on", 5, {"a": "b"}, True):   # a string used to become one command per character
            self.assertEqual(self.lines("", {"audio": {"init": bad}}), [], bad)

    def test_blank_or_unparseable_lines_give_no_command(self):
        self.assertEqual(self.lines("   \n'unterminated\n", {"audio": {"init": [" ", "\t"]}}), [])


if __name__ == "__main__":
    unittest.main()
