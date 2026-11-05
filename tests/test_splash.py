"""splash.read_conf: a bad splash.json next to the video must fall back to the defaults."""
import json
import os
import shutil
import sys
import tempfile
import unittest

from tests import helpers

sys.path.insert(0, helpers.RGOS)
try:
    import splash
finally:
    sys.path.remove(helpers.RGOS)


class ReadConfTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rgos-splash-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.video = os.path.join(self.dir, "splash.mjpg")
        open(self.video, "wb").close()

    def conf(self, text):
        with open(os.path.join(self.dir, "splash.json"), "w") as f:
            f.write(text)
        return splash.read_conf(self.video)

    def test_json_that_is_not_an_object_gives_defaults(self):
        for text in ("[]", "null", "5", '"fast"', "[1, 2]"):     # used to raise AttributeError
            self.assertEqual(self.conf(text), {"fps": 24.0, "sound": None}, text)

    def test_unparsable_json_gives_defaults(self):
        self.assertEqual(self.conf("{oops"), {"fps": 24.0, "sound": None})

    def test_bad_values_fall_back_one_by_one(self):
        self.assertEqual(self.conf(json.dumps({"fps": "fast", "sound": 5})), {"fps": 24.0, "sound": None})
        self.assertEqual(self.conf(json.dumps({"fps": 500}))["fps"], 24.0)      # out of range

    def test_good_values_are_used(self):
        wav = os.path.join(self.dir, "boot.wav")
        open(wav, "wb").close()
        self.assertEqual(self.conf(json.dumps({"fps": 30, "sound": "boot.wav"})), {"fps": 30.0, "sound": wav})


if __name__ == "__main__":
    unittest.main()
