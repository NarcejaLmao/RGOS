"""rgos-volume: choosing the ALSA card from hw.json."""
import importlib.machinery
import importlib.util
import os
import sys
import unittest
from unittest import mock

from tests import helpers


def load_volume():
    loader = importlib.machinery.SourceFileLoader("rgos_volume", os.path.join(helpers.RGOS, "rgos-volume"))
    spec = importlib.util.spec_from_loader("rgos_volume", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class MixerCardTests(unittest.TestCase):
    def mixer(self, audio_conf):
        vol = load_volume()
        fake_hw = mock.MagicMock(name="hwctl")
        fake_hw.audio_card.return_value = 3               # what auto-detection would pick
        fake_hw.audio_enable.return_value = []
        with mock.patch.dict(sys.modules, {"hwctl": fake_hw}), \
                mock.patch.object(vol, "read_json", return_value={"audio": audio_conf}), \
                mock.patch.object(vol, "run", return_value=""), \
                mock.patch.object(vol, "log"):
            return vol.Mixer()

    def test_card_zero_is_honoured(self):
        self.assertEqual(self.mixer({"card": 0}).card, 0)       # used to fall back to auto-detection
        self.assertEqual(self.mixer({"card": "0"}).card, 0)

    def test_other_card_numbers_are_honoured(self):
        self.assertEqual(self.mixer({"card": 2}).card, 2)

    def test_unset_or_bad_card_is_auto_detected(self):
        for val in (None, "", False, "x", -1):
            self.assertEqual(self.mixer({"card": val}).card, 3, val)
        self.assertEqual(self.mixer({}).card, 3)


if __name__ == "__main__":
    unittest.main()
