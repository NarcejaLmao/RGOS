"""Screen-fit rules: the TSP's 854x480 canvas must fill its 1280x720 panel, the RG34XX-SP's 720x480 must stay undistorted."""
import os, sys, unittest, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "overlay", "opt", "rgos"))
import fbout

class Fit(unittest.TestCase):
    def test_tsp_fills_panel(self):
        self.assertEqual(fbout.fit_rect((854, 480), (1280, 720)), (0, 0, 1280, 720))
    def test_3_2_is_letterboxed_not_stretched(self):
        self.assertEqual(fbout.fit_rect((720, 480), (1280, 720)), (100, 0, 1080, 720))
    def test_rg35xx_sp_native_and_letterboxed(self):
        self.assertEqual(fbout.fit_rect((640, 480), (640, 480)), (0, 0, 640, 480))     # its own panel: no scaling, no bars
        self.assertEqual(fbout.fit_rect((640, 480), (1280, 720)), (160, 0, 960, 720))  # 4:3 on a 16:9 screen: bars, never stretched
    def test_native_size(self):
        self.assertEqual(fbout.fit_rect((720, 480), (720, 480)), (0, 0, 720, 480))
    def test_clearly_different_aspect_still_bars(self):
        x, y, w, h = fbout.fit_rect((854, 480), (1280, 800))
        self.assertEqual((x, w), (0, 1280)); self.assertGreater(y, 0)
    def test_menu_canvas_sizes(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "overlay", "opt", "rgos", "menu.py")).read()
        self.assertIn("W, H = 720, 480", src)        # the RG34XX-SP's 3:2 panel is the only canvas

if __name__ == "__main__":
    unittest.main()
