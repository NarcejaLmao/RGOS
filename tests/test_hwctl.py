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


if __name__ == "__main__":
    unittest.main()
