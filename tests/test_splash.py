"""Startup splash: MJPEG splitting, file search, config, frame timing, and the shipped videos. No pygame or display needed."""
import io, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
OVL = os.path.join(HERE, "..", "overlay")
sys.path.insert(0, os.path.join(OVL, "opt", "rgos"))
import splash

try:
    from PIL import Image
except ImportError:
    Image = None


def jpegs(n, size=(32, 24)):
    out = []
    for i in range(n):
        b = io.BytesIO()
        Image.new("RGB", size, (i * 20 % 256, 40, 90)).save(b, "JPEG")
        out.append(b.getvalue())
    return out


class Clock:
    def __init__(self):
        self.t = 100.0

    def now(self):
        return self.t

    def sleep(self, d):
        self.t += max(d, 0.001)


class SplitTest(unittest.TestCase):
    @unittest.skipIf(Image is None, "Pillow missing")
    def test_split_roundtrip(self):
        fr = jpegs(5)
        data = b"".join(fr)
        spans = splash.split_mjpeg(data)
        self.assertEqual(len(spans), 5)
        self.assertEqual([data[s:e] for s, e in spans], fr)

    def test_split_empty_and_garbage(self):
        self.assertEqual(splash.split_mjpeg(b""), [])
        self.assertEqual(splash.split_mjpeg(b"not a video"), [])


class FindTest(unittest.TestCase):
    def test_search_order(self):
        with tempfile.TemporaryDirectory() as d:
            roms, etc = os.path.join(d, "roms"), os.path.join(d, "etc")
            os.makedirs(os.path.join(roms, "splash"))
            os.makedirs(etc)
            self.assertIsNone(splash.find_splash("tsp", roms, etc))
            open(os.path.join(etc, "splash.mjpg"), "wb").write(b"x")
            self.assertEqual(splash.find_splash("tsp", roms, etc), os.path.join(etc, "splash.mjpg"))
            open(os.path.join(etc, "splash-tsp.mjpg"), "wb").write(b"x")
            self.assertEqual(splash.find_splash("tsp", roms, etc), os.path.join(etc, "splash-tsp.mjpg"))
            self.assertEqual(splash.find_splash("rg35xx-sp", roms, etc), os.path.join(etc, "splash.mjpg"))
            open(os.path.join(roms, "splash", "splash.mjpg"), "wb").write(b"x")          # the card wins
            self.assertEqual(splash.find_splash("tsp", roms, etc), os.path.join(roms, "splash", "splash.mjpg"))

    def test_empty_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "splash.mjpg"), "wb").close()
            self.assertIsNone(splash.find_splash("tsp", d, d))


class ConfTest(unittest.TestCase):
    def test_defaults_and_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            v = os.path.join(d, "a.mjpg")
            open(v, "wb").write(b"x")
            self.assertEqual(splash.read_conf(v), {"fps": 24.0, "sound": None})
            open(os.path.join(d, "a.wav"), "wb").write(b"x")                                 # same-name wav is picked up
            self.assertEqual(splash.read_conf(v)["sound"], os.path.join(d, "a.wav"))
            open(os.path.join(d, "a.json"), "w").write(json.dumps({"fps": 30, "sound": "b.wav"}))
            c = splash.read_conf(v)
            self.assertEqual((c["fps"], c["sound"]), (30.0, None))                           # b.wav does not exist
            open(os.path.join(d, "b.wav"), "wb").write(b"x")
            self.assertEqual(splash.read_conf(v)["sound"], os.path.join(d, "b.wav"))

    def test_bad_values_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            v = os.path.join(d, "a.mjpg")
            for bad in ('{"fps": 0}', '{"fps": 999}', '{"fps": "fast"}', "not json"):
                open(os.path.join(d, "a.json"), "w").write(bad)
                self.assertEqual(splash.read_conf(v)["fps"], 24.0, bad)

    def test_mode_of(self):
        self.assertEqual(splash.mode_of({}), "on")
        for m in splash.MODES:
            self.assertEqual(splash.mode_of({"splash": m}), m)
        self.assertEqual(splash.mode_of({"splash": "loud"}), "on")


class PlayTest(unittest.TestCase):
    def run_play(self, n, fps, per_frame=0.0, skip_at=None, decode=None):
        clk, shown = Clock(), []
        frames = [(i, i + 1) for i in range(n)]
        data = bytes(range(n))

        def dec(b):
            clk.t += per_frame                       # a slow decoder
            return b[0]
        got = splash.play(data, frames, fps, decode or dec, shown.append,
                          lambda: skip_at is not None and clk.t - 100.0 >= skip_at, clk.now, clk.sleep)
        return got, shown, clk

    def test_plays_every_frame_in_order(self):
        got, shown, clk = self.run_play(24, 24)
        self.assertEqual(shown, list(range(24)))
        self.assertAlmostEqual(clk.t - 100.0, 1.0, delta=0.1)                              # 24 frames at 24 fps = 1 s

    def test_slow_decoder_drops_frames_but_keeps_time(self):
        got, shown, clk = self.run_play(48, 24, per_frame=0.1)                              # 10 fps decoder, 24 fps video
        self.assertLess(got, 48)
        self.assertEqual(shown, sorted(shown))
        self.assertAlmostEqual(clk.t - 100.0, 2.0, delta=0.25)                             # still ends after about 2 s

    def test_skip_stops_early(self):
        got, shown, _ = self.run_play(48, 24, skip_at=0.5)
        self.assertTrue(0 < got < 20, got)

    def test_broken_frames(self):
        def dec(b):
            raise ValueError("bad jpeg")
        got, shown, _ = self.run_play(30, 24, decode=dec)
        self.assertEqual((got, shown), (0, []))                                             # gives up after 5 failures
        calls = []

        def flaky(b):
            calls.append(b)
            if b[0] == 3:
                raise ValueError
            return b[0]
        got, shown, _ = self.run_play(10, 24, decode=flaky)
        self.assertNotIn(3, shown)
        self.assertIn(9, shown)

    def test_no_frames(self):
        self.assertEqual(splash.play(b"", [], 24, None, None, lambda: False), 0)


@unittest.skipIf(Image is None, "Pillow missing")
class ShippedVideosTest(unittest.TestCase):
    SIZES = {"rg34xx-sp": (720, 480)}        # canvas size in menu.py

    def test_every_device_has_a_matching_video(self):
        d = os.path.join(OVL, "etc", "rgos", "splash")
        for dev, size in self.SIZES.items():
            data = open(os.path.join(d, "splash-%s.mjpg" % dev), "rb").read()
            spans = splash.split_mjpeg(data)
            conf = splash.read_conf(os.path.join(d, "splash-%s.mjpg" % dev))
            self.assertEqual(len(spans), 144, dev)                                             # 6 s at 24 fps
            self.assertEqual(conf["fps"], 24.0)
            self.assertTrue(conf["sound"] and os.path.isfile(conf["sound"]), dev)
            for i in (0, 70, 143):                                                              # first, middle, last decode at canvas size
                im = Image.open(io.BytesIO(data[spans[i][0]:spans[i][1]]))
                im.load()
                self.assertEqual(im.size, size, dev)
            self.assertLess(len(data), 3000000, dev)

    def test_menu_canvas_sizes_match(self):
        src = open(os.path.join(OVL, "opt", "rgos", "menu.py")).read()
        self.assertIn("W, H = 720, 480", src)


class BootSplashServiceTest(unittest.TestCase):
    """The video must start from its own early service, not from the menu."""
    def read(self, *p):
        return open(os.path.join(OVL, *p)).read()

    def test_service_is_early_and_non_blocking(self):
        u = self.read("etc", "systemd", "system", "rgos-splash.service")
        self.assertIn("DefaultDependencies=no", u)
        self.assertIn("Before=rgos.service", u)
        self.assertNotIn("Before=sysinit.target", u)          # the rest of boot must not wait for the video
        self.assertIn("TimeoutStartSec=", u)
        self.assertNotRegex(u, r"(?m)^(After|Requires|Wants)=.*(local-fs|sdcard|udev-settle)")   # no waiting for mounts

    def test_launcher_waits_for_the_video(self):
        u = self.read("etc", "systemd", "system", "rgos.service")
        self.assertRegex(u, r"(?m)^After=.*rgos-splash\.service")

    def test_script_marks_done_so_menu_skips_it(self):
        s = self.read("opt", "rgos", "rgos-splash")
        self.assertIn('"splash-done"', s)
        self.assertIn("mark_done()", s)
        self.assertIn('SPLASH_DONE = os.path.join(RUN_DIR, "splash-done")', self.read("opt", "rgos", "menu.py"))


if __name__ == "__main__":
    unittest.main()
