"""Performance helpers in menu.py (pygame stubbed): render/size caching and read throttling."""
import os, sys, unittest
from unittest import mock

OPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay", "opt", "rgos")
sys.path.insert(0, OPT)


def load():
    sys.modules.pop("menu", None)
    with mock.patch.dict(sys.modules, {"pygame": mock.MagicMock()}):
        import menu
        return menu


class FakeFont:
    def __init__(self): self.renders = self.sizes = 0
    def render(self, text, aa, color, *bg):
        self.renders += 1
        return object()
    def size(self, text):
        self.sizes += 1
        return (len(text) * 8, 16)
    def get_height(self): return 16


class T(unittest.TestCase):
    def test_render_is_cached_per_text_and_colour(self):
        m, f = load(), FakeFont()
        cf = m.CFont(f)
        a = cf.render("Games", True, (1, 2, 3))
        self.assertIs(a, cf.render("Games", True, (1, 2, 3)))
        self.assertIsNot(a, cf.render("Games", True, (9, 9, 9)))
        self.assertIsNot(a, cf.render("Other", True, (1, 2, 3)))
        self.assertEqual(f.renders, 3)

    def test_size_cached_and_passthrough(self):
        m, f = load(), FakeFont()
        cf = m.CFont(f)
        self.assertEqual(cf.size("abc"), (24, 16)); cf.size("abc")
        self.assertEqual(f.sizes, 1)
        self.assertEqual(cf.get_height(), 16)

    def test_cache_is_bounded(self):
        m, f = load(), FakeFont()
        cf = m.CFont(f)
        for i in range(2000):
            cf.render("t%d" % i, True, (0, 0, 0))
        self.assertLess(len(cf._r), 700)

    def test_throttled_reads_at_most_once_per_interval(self):
        m = load()
        calls = []
        g = m.throttled(lambda: calls.append(1) or len(calls), 60)
        self.assertEqual((g(), g(), g()), (1, 1, 1))
        self.assertEqual(len(calls), 1)
        g2 = m.throttled(lambda: calls.append(1) or len(calls), 0)
        g2(); g2()
        self.assertEqual(len(calls), 3)

    def test_entry_point_is_importable_and_session_uses_it(self):
        m = load()
        self.assertTrue(callable(m.run))
        sess = open(os.path.join(OPT, "rgos-session")).read()
        self.assertIn("menu.run()", sess)
        self.assertNotIn("python3 /opt/rgos/menu.py", sess)


if __name__ == "__main__":
    unittest.main()
