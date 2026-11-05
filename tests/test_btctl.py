"""btctl: the saved Bluetooth on/off setting is applied at boot."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from tests import helpers

sys.path.insert(0, helpers.RGOS)
try:
    import btctl
finally:
    sys.path.remove(helpers.RGOS)


class ApplyPowerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rgos-bt-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        p = mock.patch.object(btctl, "CONF", os.path.join(self.dir, "bt.json"))
        p.start()
        self.addCleanup(p.stop)

    def power_calls(self, conf):
        if conf is not None:
            with open(btctl.CONF, "w") as f:
                json.dump(conf, f)
        with mock.patch.object(btctl, "run", return_value=(0, "")) as run:
            btctl.apply_power()
        return [c.args[0] for c in run.call_args_list]

    def test_enabled_powers_on(self):
        self.assertEqual(self.power_calls({"enabled": True}), [["bluetoothctl", "power", "on"]])

    def test_disabled_powers_off(self):                       # used to do nothing, so BlueZ's AutoEnable turned it back on
        self.assertEqual(self.power_calls({"enabled": False}), [["bluetoothctl", "power", "off"]])

    def test_no_settings_file_defaults_to_on(self):
        self.assertEqual(self.power_calls(None), [["bluetoothctl", "power", "on"]])


if __name__ == "__main__":
    unittest.main()
