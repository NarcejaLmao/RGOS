"""The menu's own device reader must understand non-standard handheld pads (no hardware needed)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "overlay", "opt", "rgos"))
import evpad

class Profiles(unittest.TestCase):
    def test_standard_pad_unchanged(self):
        p = evpad.make_profile({0x130, 0x131, 0x136, 0x13b})
        self.assertEqual(p, {0x130: 0x130, 0x131: 0x131, 0x136: 0x136, 0x13b: 0x13b})
    def test_keyboard_style_handheld(self):
        p = evpad.make_profile({103, 108, 105, 106, 57, 29, 28, 97, 1})
        self.assertEqual((p[103], p[57], p[29], p[28], p[97], p[1]), (0x220, 0x131, 0x130, 0x13b, 0x13a, 0x13c))
    def test_joystick_layout(self):
        p = evpad.make_profile({0x120, 0x121, 0x128, 0x129})
        self.assertEqual((p[0x120], p[0x121], p[0x128], p[0x129]), (0x130, 0x131, 0x13a, 0x13b))
    def test_unknown_buttons_assigned_in_order(self):
        p = evpad.make_profile({0x100, 0x101, 0x102, 0x103})
        self.assertEqual([p[c] for c in (0x100, 0x101, 0x102, 0x103)], [0x130, 0x131, 0x133, 0x134])
    def test_keyboard_codes_ignored_when_real_pad_present(self):
        p = evpad.make_profile({0x130, 0x131, 57, 103})
        self.assertNotIn(57, p); self.assertEqual(p[103], 0x220)
    def test_power_and_volume_keys_not_mapped(self):
        self.assertEqual(evpad.make_profile({116, 114, 115}), {})
    def test_ioctl_constant(self):
        self.assertEqual(evpad.EVIOCGBIT_KEY, (2 << 30) | (96 << 16) | (ord("E") << 8) | 0x21)

if __name__ == "__main__":
    unittest.main()
