"""hwctl.Motor: GPIO selection."""
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


if __name__ == "__main__":
    unittest.main()
