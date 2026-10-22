"""Event-driven idle loops: daemons block in select() on the input fds instead of polling with sleep()."""
import importlib.machinery, importlib.util, os, sys, time, unittest

OPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay", "opt", "rgos")
sys.path.insert(0, OPT)
import evpad


def load(name):
    loader = importlib.machinery.SourceFileLoader("idle_" + name.replace("-", "_"), os.path.join(OPT, name))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


class Wait(unittest.TestCase):
    def test_returns_at_once_when_data_is_ready(self):
        r, w = os.pipe(); os.write(w, b"x")
        p = evpad.EvPads(); p.fds["/p"] = r
        t = time.time(); p.wait(2.0)
        self.assertLess(time.time() - t, 0.5)

    def test_blocks_for_the_timeout_when_idle(self):
        r, w = os.pipe()
        p = evpad.EvPads(); p.fds["/p"] = r
        t = time.time(); p.wait(0.2)
        self.assertGreaterEqual(time.time() - t, 0.15)

    def test_no_devices_still_waits_and_bad_fd_does_not_raise(self):
        p = evpad.EvPads()
        t = time.time(); p.wait(0.1)
        self.assertGreaterEqual(time.time() - t, 0.08)
        p.fds["/gone"] = 987654
        p.wait(0.1)


class VolumeTimeout(unittest.TestCase):
    def test_timeouts(self):
        f = load("rgos-volume").idle_timeout
        self.assertEqual(f(100, True, 0, False, 0), 3.0)             # idle: only the device-rescan bound
        self.assertAlmostEqual(f(100, False, 101.5, False, 0), 1.5)  # mixer not ready: wake for the retry
        self.assertAlmostEqual(f(100, True, 0, True, 99.9), 0.15)    # OSD throttle pending
        self.assertEqual(f(100, False, 90, True, 50), 0.0)           # never negative


class HwTimeout(unittest.TestCase):
    def test_timeouts(self):
        f = load("rgos-hw").idle_timeout
        self.assertEqual(f(0, None, False, 1.5, 30, 0, False), 1.0)               # capped so SIGTERM is seen
        self.assertAlmostEqual(f(0, None, False, 1.5, 0.4, 0, False), 0.4)        # battery tick due
        self.assertAlmostEqual(f(10, 9.5, False, 1.5, 99, 0, False), 1.0)         # long press due in 1.0 s
        self.assertAlmostEqual(f(10, 8.9, False, 1.5, 99, 0, False), 0.4)
        self.assertEqual(f(10, 8.0, True, 1.5, 99, 0, False), 1.0)                # long press already handled
        self.assertAlmostEqual(f(10, None, False, 1.5, 99, 10.2, True), 0.2)      # audio retry pending
        self.assertEqual(f(10, 0, False, 1.5, 99, 0, False), 0.0)


if __name__ == "__main__":
    unittest.main()
