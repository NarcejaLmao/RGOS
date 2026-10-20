"""Auto-mapping tests for the input hub's Translator (no hardware)."""
import importlib.machinery, importlib.util, os, unittest
p = os.path.join(os.path.dirname(__file__), "..", "overlay", "opt", "rgos", "rgos-sticks")
spec = importlib.util.spec_from_loader("rs", importlib.machinery.SourceFileLoader("rs", p))
rs = importlib.util.module_from_spec(spec); spec.loader.exec_module(rs)
K, A = rs.EV_KEY, rs.EV_ABS

def tr(**kw):
    am = {k: v for k, v in rs.SRC_AXES.items()}
    t = rs.Translator({}, am, None, 0.1, False, True)
    t.explicit_axes = False          # as run() does when input.json has no axis_map
    return t

class AutoMap(unittest.TestCase):
    def test_standard_pad_passthrough(self):
        t = tr(); t.learn("pad", {0x130, 0x131, 0x136}, {0, 1, 3, 4})
        self.assertEqual(t.key("pad", 0x131, 1), [(K, 0x131, 1)])

    def test_unknown_codes_assigned_in_order(self):
        t = tr(); n = t.learn("odd", {0x2c0 - 0x2c0 + 0x100 + i for i in (0, 1, 2)} | {0x13f}, set())
        # 0x100..0x102 are BTN_0..2: unknown to the tables -> filled B, A, X in code order
        self.assertEqual(t.key("odd", 0x100, 1), [(K, 0x130, 1)])
        self.assertEqual(t.key("odd", 0x101, 1), [(K, 0x131, 1)])
        self.assertEqual(t.key("odd", 0x102, 1), [(K, 0x133, 1)])

    def test_joystick_layout_aliases(self):
        t = tr(); t.learn("js", {0x120, 0x121, 0x128, 0x129}, {0, 1})
        self.assertEqual(t.key("js", 0x120, 1), [(K, 0x130, 1)])
        self.assertEqual(t.key("js", 0x129, 1), [(K, 0x13b, 1)])

    def test_keyboard_style_handheld(self):
        t = tr(); t.learn("TRIMUI Player1", {57, 29, 28, 97, 103, 108, 105, 106}, set())
        self.assertEqual(t.key("TRIMUI Player1", 57, 1), [(K, 0x131, 1)])
        self.assertEqual(t.key("TRIMUI Player1", 28, 1), [(K, 0x13b, 1)])
        self.assertEqual(t.key("TRIMUI Player1", 103, 1), [(K, 0x220, 1)])

    def test_z_rz_are_triggers_when_rx_ry_exist(self):
        t = tr(); t.learn("x", {0x130}, {0, 1, 2, 3, 4, 5})
        self.assertEqual(t.abs("x", 2, 255, (0, 255)), [(K, 0x138, 1)])
        self.assertEqual(t.abs("x", 2, 0, (0, 255)), [(K, 0x138, 0)])
        self.assertEqual([e for e in t.abs("x", 3, 32767, (-32767, 32767))][0][1], rs.ABS_CODES[2])

    def test_z_rz_are_right_stick_without_rx_ry(self):
        t = tr(); t.learn("y", {0x130}, {0, 1, 2, 5})
        self.assertEqual(t.abs("y", 2, 32767, (-32767, 32767))[0][1], rs.ABS_CODES[2])
        self.assertEqual(t.abs("y", 5, 32767, (-32767, 32767))[0][1], rs.ABS_CODES[3])

    def test_hat_dpad(self):
        t = tr(); t.learn("h", {0x130}, {0x10, 0x11})
        self.assertEqual(t.abs("h", 0x10, -1, (-1, 1)), [(K, 0x222, 1)])

    def test_gamepad_codes_shadow_keyboard_codes(self):
        t = tr(); t.learn("m", {0x130, 0x131, 57, 103}, set())
        self.assertEqual(t.key("m", 57, 1), [])
        self.assertEqual(t.key("m", 103, 1), [(K, 0x220, 1)])

if __name__ == "__main__":
    unittest.main()
