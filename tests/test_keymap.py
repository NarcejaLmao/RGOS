"""rgos-input load_keymap: a damaged input.json / button-map.json must not stop the hub or drop good mappings."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests import helpers

hub = helpers.load_hub()


class LoadKeymapTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rgos-keymap-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        p = mock.patch.object(hub, "BTNMAP", os.path.join(self.dir, "button-map.json"))
        p.start()
        self.addCleanup(p.stop)

    def wizard(self, data):
        with open(hub.BTNMAP, "w") as f:
            json.dump(data, f)

    def test_keymap_that_is_not_an_object_is_ignored(self):
        for bad in ([1], "x", 5, True):                          # used to raise AttributeError and stop the hub
            self.assertEqual(hub.load_keymap({"keymap": bad}), {}, bad)

    def test_input_json_keymap_skips_bad_entries(self):
        km = hub.load_keymap({"keymap": {"5": 7, "bad": 1, "6": "0x10"}})
        self.assertEqual(km, {(None, 1, 5, 0): 7, (None, 1, 6, 0): 16})

    def test_wizard_map_is_read(self):
        self.wizard({"keys": {"pad|1:305:0": 304, "|3:16:-1": 546}})
        self.assertEqual(hub.load_keymap({}), {("pad", 1, 305, 0): 304, (None, 3, 16, -1): 546})

    def test_one_bad_wizard_entry_keeps_the_others(self):
        self.wizard({"keys": {"a|1:2:0": 304, "oops": 1, "b|1:3:0": "x", "c|1:4:0": 306, "d|1:5:0": [1]}})
        self.assertEqual(hub.load_keymap({}), {("a", 1, 2, 0): 304, ("c", 1, 4, 0): 306})

    def test_out_of_range_number_in_the_wizard_map_is_just_a_bad_entry(self):
        with open(hub.BTNMAP, "w") as f:                          # json.load turns 1e999 into inf; int(inf) raises OverflowError
            f.write('{"keys": {"a|1:2:0": 304, "b|1:3:0": 1e999, "c|1:4:0": 306}}')
        self.assertEqual(hub.load_keymap({}), {("a", 1, 2, 0): 304, ("c", 1, 4, 0): 306})

    def test_unusable_wizard_files_give_an_empty_map(self):
        for data in ([], {"keys": 5}, {"keys": []}, {"keys": None}):
            self.wizard(data)
            self.assertEqual(hub.load_keymap({}), {}, data)
        os.unlink(hub.BTNMAP)
        self.assertEqual(hub.load_keymap({}), {})


if __name__ == "__main__":
    unittest.main()
