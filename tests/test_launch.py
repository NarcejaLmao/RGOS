"""rgos-launch: a damaged state.json must not stop a game from launching."""
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

from tests import helpers


def load_launch():
    loader = importlib.machinery.SourceFileLoader("rgos_launch", os.path.join(helpers.RGOS, "rgos-launch"))
    spec = importlib.util.spec_from_loader("rgos_launch", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class PickCoreTests(unittest.TestCase):
    def setUp(self):
        self.launch = load_launch()
        p = mock.patch.object(self.launch, "core_file", lambda c: "/cores/%s.so" % c if isinstance(c, str) and c else "")
        p.start()
        self.addCleanup(p.stop)

    def pick(self, state):
        return self.launch.pick_core("snes", state, lambda sid: "snes9x", lambda sid, cores=None: ["bsnes"])

    def test_users_choice_wins(self):
        self.assertEqual(self.pick({"emulators": {"snes": "bsnes"}}), "bsnes")

    def test_no_choice_uses_the_system_default(self):
        self.assertEqual(self.pick({}), "snes9x")
        self.assertEqual(self.pick({"emulators": {}}), "snes9x")

    def test_state_that_is_not_the_expected_shape_falls_back_to_the_default(self):
        for bad in ([], None, "x", 5, {"emulators": []}, {"emulators": "a"}, {"emulators": {"snes": ["bsnes"]}},
                    {"emulators": {"snes": 7}}):                # used to raise AttributeError (or pass a non-string core on)
            self.assertEqual(self.pick(bad), "snes9x", bad)


if __name__ == "__main__":
    unittest.main()
