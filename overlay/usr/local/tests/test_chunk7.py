#!/usr/bin/env python3
"""Chunk 7 tests: hwctl (backlight/battery/audio/motor), ffpad (rumble handshake) and the rgos-hw daemon logic, all
against fake sysfs trees and a scripted fake input device (no hardware needed). Run: python3 tests/test_chunk7.py"""
import ctypes, importlib.machinery, importlib.util, json, os, struct, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
OPT = os.path.join(HERE, "..", "overlay", "opt", "rgos")
sys.path.insert(0, OPT)
import hwctl, ffpad  # noqa: E402


def mk(root, rel, text=""):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(str(text))
    return p


def rdf(root, rel):
    with open(os.path.join(root, rel)) as f:
        return f.read()


class Backlight(unittest.TestCase):
    def test_sysfs(self):
        with tempfile.TemporaryDirectory() as r:
            mk(r, "class/backlight/backlight0/brightness", 100)
            mk(r, "class/backlight/backlight0/max_brightness", 255)
            bl = hwctl.Backlight({"backlight": {"min_pct": 5}}, sys_root=r)
            self.assertEqual(bl.kind, "sysfs")
            self.assertTrue(bl.set(50)); self.assertEqual(rdf(r, "class/backlight/backlight0/brightness"), "128")
            self.assertEqual(bl.get(), 50)
            bl.set(1);   self.assertEqual(bl.get(), 5)          # raised to min_pct
            bl.set(0);   self.assertEqual(rdf(r, "class/backlight/backlight0/brightness"), "0")
            bl.set(250); self.assertEqual(rdf(r, "class/backlight/backlight0/brightness"), "255")

    def test_gamma(self):
        with tempfile.TemporaryDirectory() as r:
            mk(r, "class/backlight/b/brightness", 0); mk(r, "class/backlight/b/max_brightness", 1000)
            bl = hwctl.Backlight({"backlight": {"gamma": 2.0}}, sys_root=r)
            bl.set(50); self.assertEqual(rdf(r, "class/backlight/b/brightness"), "250")
            self.assertEqual(bl.get(), 50)

    def test_dispdbg(self):
        with tempfile.TemporaryDirectory() as r:
            d = "kernel/debug/dispdbg/"
            for f in ("name", "command", "param", "start"):
                mk(r, d + f)
            bl = hwctl.Backlight({}, sys_root=r)
            self.assertEqual(bl.kind, "dispdbg")
            bl.set(100)
            self.assertEqual((rdf(r, d + "name"), rdf(r, d + "command"), rdf(r, d + "param"), rdf(r, d + "start")),
                             ("lcd0", "setbl", "255", "1"))
            self.assertEqual(bl.get(), 100)

    def test_none(self):
        with tempfile.TemporaryDirectory() as r:
            bl = hwctl.Backlight({}, sys_root=r)
            self.assertFalse(bl.available); self.assertFalse(bl.set(50)); self.assertIsNone(bl.get())


class Battery(unittest.TestCase):
    def setUp(self):
        self.r = tempfile.TemporaryDirectory(); self.addCleanup(self.r.cleanup)

    def test_capacity(self):
        mk(self.r.name, "class/power_supply/ac/type", "Mains")
        mk(self.r.name, "class/power_supply/axp2202-battery/type", "Battery")
        mk(self.r.name, "class/power_supply/axp2202-battery/capacity", 42)
        mk(self.r.name, "class/power_supply/axp2202-battery/status", "Discharging")
        b = hwctl.Battery({}, sys_root=self.r.name)
        x = b.read()
        self.assertEqual((x["pct"], x["discharging"], x["plugged"], x["estimated"]), (42, True, False, False))

    def test_charging_full(self):
        mk(self.r.name, "class/power_supply/b/type", "Battery"); mk(self.r.name, "class/power_supply/b/capacity", 100)
        for st, ch, pl in (("Charging", True, True), ("Full", False, True), ("Not charging", False, True)):
            mk(self.r.name, "class/power_supply/b/status", st)
            x = hwctl.Battery({}, sys_root=self.r.name).read()
            self.assertEqual((x["charging"], x["plugged"], x["discharging"]), (ch, pl, False))

    def test_voltage_estimate(self):
        mk(self.r.name, "class/power_supply/b/type", "Battery"); mk(self.r.name, "class/power_supply/b/voltage_now", 3725000)
        mk(self.r.name, "class/power_supply/b/status", "Discharging")
        x = hwctl.Battery({}, sys_root=self.r.name).read()
        self.assertTrue(x["estimated"]); self.assertEqual(x["pct"], 50)

    def test_missing(self):
        self.assertIsNone(hwctl.Battery({}, sys_root=self.r.name).read())


class Audio(unittest.TestCase):
    def test_card(self):
        t = " 0 [audiocodec     ]: sun50iw10-codec - audiocodec\n                      audiocodec\n 1 [sndhdmi        ]: sndhdmi - sndhdmi\n"
        self.assertEqual(hwctl.audio_card(t), 0)
        t2 = " 0 [sndhdmi        ]: sndhdmi - sndhdmi\n 1 [rockchip       ]: codec - codec\n"
        self.assertEqual(hwctl.audio_card(t2), 1)
        self.assertEqual(hwctl.audio_card(""), 0)

    def test_enable(self):
        calls = []
        def fake(args, timeout=5):
            calls.append(args)
            if args[-1] == "scontrols":
                return "Simple mixer control 'LINEOUT',0\nSimple mixer control 'Mic1 Boost',0\nSimple mixer control 'Speaker',0\n"
            if args[-2:] == ["sget", "LINEOUT"] or args[-2:] == ["sget", "Speaker"]:
                return "  Capabilities: pvolume pswitch\n"
            return ""
        with mock.patch.object(hwctl, "_run", fake):
            done = hwctl.audio_enable(0)
        self.assertEqual(done, ["LINEOUT", "Speaker"])
        self.assertIn(["amixer", "-c", "0", "-q", "sset", "LINEOUT", "unmute"], calls)


class Motor(unittest.TestCase):
    def test_gpio(self):
        with tempfile.TemporaryDirectory() as r:
            g = os.path.join(r, "gpio"); mk(g, "gpio227/value", 0); mk(g, "gpio227/direction", "in"); mk(g, "export")
            m = hwctl.Motor({"rumble": {"gpio": 227}}, gpio_root=g)
            self.assertEqual(m.mode, "gpio"); self.assertEqual(rdf(g, "gpio227/direction"), "low")
            m.set(True); self.assertEqual(rdf(g, "gpio227/value"), "1")
            m.set(False); self.assertEqual(rdf(g, "gpio227/value"), "0")
            m2 = hwctl.Motor({"rumble": {"gpio": 227, "active_low": True}}, gpio_root=g)
            self.assertEqual(rdf(g, "gpio227/direction"), "high")
            m2.set(True); self.assertEqual(rdf(g, "gpio227/value"), "0")

    def test_gpio_missing_node_disables(self):
        with tempfile.TemporaryDirectory() as r:
            m = hwctl.Motor({"rumble": {"gpio": 227}}, sys_root=r, gpio_root=os.path.join(r, "nogpio"))
            self.assertFalse(m.available)

    def test_timed_and_led(self):
        with tempfile.TemporaryDirectory() as r:
            mk(r, "class/timed_output/vibrator/enable", 0)
            m = hwctl.Motor({}, sys_root=r); self.assertEqual(m.mode, "timed")
            m.set(True); self.assertEqual(rdf(r, "class/timed_output/vibrator/enable"), "5000")
            m.set(False); self.assertEqual(rdf(r, "class/timed_output/vibrator/enable"), "0")
        with tempfile.TemporaryDirectory() as r:
            mk(r, "class/leds/vibrator/brightness", 0)
            m = hwctl.Motor({}, sys_root=r); self.assertEqual(m.mode, "led")
            m.set(True); self.assertEqual(rdf(r, "class/leds/vibrator/brightness"), "1")


class Conf(unittest.TestCase):
    def test_merge_and_missing(self):
        with tempfile.TemporaryDirectory() as r:
            base, user = os.path.join(r, "hw.json"), os.path.join(r, "user.json")
            json.dump({"backlight": {"min_pct": 5, "step_pct": 10}, "x": 1}, open(base, "w"))
            json.dump({"backlight": {"step_pct": 20}}, open(user, "w"))
            c = hwctl.load_conf(base, user)
            self.assertEqual((c["backlight"]["min_pct"], c["backlight"]["step_pct"], c["x"]), (5, 20, 1))
            self.assertIsNone(hwctl.load_conf(os.path.join(r, "none.json"), user))
            open(base, "w").write("{not json")
            self.assertIsNone(hwctl.load_conf(base, user))

    def test_shipped_config_parses(self):
        # the RG34XX-SP has no hardware profile (no battery gauge, brightness keys or power-key handling from RG OS)
        d = os.path.join(HERE, "..", "overlay", "etc", "rgos")
        self.assertEqual([f for f in os.listdir(d) if f.startswith("hw-")], [])


class FF(unittest.TestCase):
    """Drives FFHandler with a pipe as the uinput fd and a fake ioctl that behaves like the kernel side."""
    def setUp(self):
        self.rfd, self.wfd = os.pipe(); os.set_blocking(self.rfd, False)
        self.now = [0.0]
        self.motor = mock.Mock()
        self.h = ffpad.FFHandler(self.rfd, self.motor, threshold=0.15, max_ms=3000, clock=lambda: self.now[0])
        self.pending = {}
        self.p = mock.patch.object(ffpad.fcntl, "ioctl", self.fake_ioctl); self.p.start()
        self.addCleanup(self.p.stop); self.addCleanup(os.close, self.rfd); self.addCleanup(os.close, self.wfd)
        self.ended = []

    def fake_ioctl(self, fd, req, buf, mut=False):
        if req == ffpad.UI_BEGIN_FF_UPLOAD:
            up = ffpad.UploadReq.from_buffer(buf); e = self.pending[up.request_id]
            ctypes.memmove(ctypes.addressof(up.effect), ctypes.addressof(e), ctypes.sizeof(e))
        elif req == ffpad.UI_BEGIN_FF_ERASE:
            er = ffpad.EraseReq.from_buffer(buf); er.effect_id = self.pending[er.request_id]
        elif req in (ffpad.UI_END_FF_UPLOAD, ffpad.UI_END_FF_ERASE):
            self.ended.append((req, struct.unpack_from("<Ii", buf, 0)))
        return 0

    def ev(self, t, c, v):
        os.write(self.wfd, struct.pack("llHHi", 0, 0, t, c, v))

    def upload(self, req_id, eid, strong, weak, length=0, typ=ffpad.FF_RUMBLE):
        e = ffpad.FFEffect(); e.type, e.id = typ, eid; e.replay.length = length
        e.u.rumble.strong_magnitude, e.u.rumble.weak_magnitude = strong, weak
        self.pending[req_id] = e
        self.ev(ffpad.EV_UINPUT, ffpad.UI_FF_UPLOAD, req_id)

    def test_play_stop(self):
        self.upload(7, 0, 0xFFFF, 0); self.ev(ffpad.EV_FF, 0, 1); self.h.service()
        self.motor.set.assert_called_with(True)
        self.assertEqual(self.ended[0], (ffpad.UI_END_FF_UPLOAD, (7, 0)))        # request 7 answered with retval 0
        self.ev(ffpad.EV_FF, 0, 0); self.h.service()
        self.motor.set.assert_called_with(False)

    def test_length_expiry_and_cap(self):
        self.upload(1, 0, 0xFFFF, 0, length=500); self.upload(2, 1, 0xFFFF, 0, length=0)
        self.ev(ffpad.EV_FF, 0, 1); self.h.service()
        self.assertAlmostEqual(self.h.next_deadline(), 0.25)                    # bounded by the default
        self.now[0] = 0.6; self.h.tick()
        self.motor.set.assert_called_with(False)
        self.ev(ffpad.EV_FF, 1, 1); self.h.service()                            # length 0 = until stopped, but capped
        self.now[0] = 3.5; self.h.tick()
        self.assertTrue(self.h.motor_on)                                        # started at 0.6 s, cap 3 s -> due at 3.6 s
        self.now[0] = 3.7; self.h.tick()
        self.motor.set.assert_called_with(False)

    def test_threshold_weak_and_non_rumble(self):
        self.upload(1, 0, 0, 0x1000); self.ev(ffpad.EV_FF, 0, 1); self.h.service()   # too weak: motor stays off
        self.assertFalse(self.h.motor_on)
        self.upload(2, 1, 0, 0xFFFF); self.ev(ffpad.EV_FF, 1, 1); self.h.service()   # strong weak-motor effect: on
        self.assertTrue(self.h.motor_on)
        self.upload(3, 2, 0, 0, typ=0x52)                                            # FF_PERIODIC: refused with EINVAL
        self.h.service()
        self.assertEqual(self.ended[-1], (ffpad.UI_END_FF_UPLOAD, (3, -22)))

    def test_erase_stops_effect(self):
        self.upload(1, 0, 0xFFFF, 0); self.ev(ffpad.EV_FF, 0, 1); self.h.service()
        self.pending[9] = 0; self.ev(ffpad.EV_UINPUT, ffpad.UI_FF_ERASE, 9); self.h.service()
        self.assertFalse(self.h.motor_on); self.assertNotIn(0, self.h.effects)

    def test_close_turns_motor_off(self):
        self.upload(1, 0, 0xFFFF, 0); self.ev(ffpad.EV_FF, 0, 1); self.h.service()
        self.h.close(); self.assertFalse(self.h.motor_on)


def load_daemon():
    loader = importlib.machinery.SourceFileLoader("rgos_hw", os.path.join(OPT, "rgos-hw"))
    spec = importlib.util.spec_from_loader("rgos_hw", loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    return mod


class Daemon(unittest.TestCase):
    """Runs rgos-hw's main loop with a fake clock, fake input events and fake sysfs."""
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        r = self.d.name
        self.mod = load_daemon()
        m = self.mod
        for name, rel in (("RUN", "run"), ("BRIGHT_SAVED", "lib/brightness"), ("BRIGHT_SHOWN", "run/brightness"),
                          ("BATT_FILE", "run/battery"), ("LOWFLAG", "run/battery-low"), ("SCREENOFF", "run/screenoff"),
                          ("ACTIVE", "run/hw-active"), ("PORT_PID", "run/port.pid")):
            setattr(m, name, os.path.join(r, rel))
        mk(r, "sys/class/backlight/backlight0/brightness", 178); mk(r, "sys/class/backlight/backlight0/max_brightness", 255)
        mk(r, "sys/class/power_supply/bat/type", "Battery"); mk(r, "sys/class/power_supply/bat/capacity", 80)
        mk(r, "sys/class/power_supply/bat/status", "Discharging")
        os.makedirs(os.path.join(r, "run"))
        self.sys = os.path.join(r, "sys")
        self.conf = {"backlight": {"default_pct": 70, "step_pct": 10}, "power_key": {"short": "screen", "long_ms": 1500, "codes": [116]},
                     "bright_combo": "select", "pause_game_on_screen_off": False, "osd_port": 0,
                     "battery": {"low_pct": 15, "critical_pct": 3, "critical_grace_s": 30}, "audio": {"enable_switches": False},
                     "rumble": {"mode": "none"}}
        self.msgs, self.poweroffs = [], []
        self.t = [1000.0]
        orig_bl, orig_bat, orig_motor = hwctl.Backlight, hwctl.Battery, hwctl.Motor
        self.patches = [
            mock.patch.object(hwctl, "load_conf", lambda *a, **k: self.conf),
            mock.patch.object(hwctl, "Backlight", lambda c=None, sys_root="/sys": orig_bl(c, sys_root=self.sys)),
            mock.patch.object(hwctl, "Battery", lambda c=None, sys_root="/sys": orig_bat(c, sys_root=self.sys)),
            mock.patch.object(hwctl, "Motor", lambda c=None, **k: orig_motor(c, sys_root=self.sys, gpio_root=os.path.join(r, "nogpio"))),
            mock.patch.object(hwctl, "retroarch_msg", lambda text, port=55355: self.msgs.append(text)),
            mock.patch.object(m.time, "time", lambda: self.t[0]),
            mock.patch.object(m.Hw, "poweroff", lambda s, why: self.poweroffs.append(why)),
        ]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def run_script(self, script, tick=0.1):
        """script: list of (time offset, [(type, code, value), ...]). Runs until it is exhausted."""
        m = self.mod
        m.STOP = False
        t0 = self.t[0]
        pending = sorted(script, key=lambda x: x[0])
        class FakePads:
            def read(_):
                out = []
                while pending and t0 + pending[0][0] <= self.t[0]:
                    out += pending.pop(0)[1]
                return out
        def fake_sleep(_):
            self.t[0] += tick
            if not pending and self.t[0] > t0 + self.horizon:
                m.STOP = True
        self.horizon = (script[-1][0] if script else 0) + 2
        with mock.patch.object(m, "EvPads", FakePads), mock.patch.object(m.time, "sleep", fake_sleep):
            m.daemon()

    def bl(self):
        return int(rdf(self.sys, "class/backlight/backlight0/brightness"))

    def test_start_restores_default_and_flags(self):
        self.run_script([])
        self.assertEqual(self.bl(), 178)                       # 70% of 255 = 178.5 -> 178 (rounded)
        self.assertFalse(os.path.exists(self.mod.ACTIVE))      # flags removed on exit

    def test_brightness_combo(self):
        SEL, UP, DN = 0x13a, 115, 114
        self.run_script([(0.5, [(1, UP, 1)]),                                    # VOL+ alone: nothing
                         (1.0, [(1, SEL, 1)]), (1.2, [(1, UP, 1), (1, UP, 0)]), (1.4, [(1, UP, 1)]),
                         (1.6, [(1, SEL, 0)]), (1.8, [(1, UP, 1)])])
        self.assertEqual(rdf(self.d.name, "lib/brightness"), "90")             # 70 + 2 steps; the others ignored
        self.assertEqual(self.bl(), 230)
        self.assertEqual(rdf(self.d.name, "run/brightness").split()[0], "90")

    def test_brightness_floor_and_ceiling(self):
        SEL, DN, UP = 0x13a, 114, 115
        self.run_script([(0.1, [(1, SEL, 1)])] + [(0.2 + i * 0.05, [(1, DN, 1)]) for i in range(12)])
        self.assertEqual(rdf(self.d.name, "lib/brightness"), "5")
        self.run_script([(0.1, [(1, SEL, 1)])] + [(0.2 + i * 0.05, [(1, UP, 1)]) for i in range(12)])
        self.assertEqual(rdf(self.d.name, "lib/brightness"), "100")

    def test_short_power_press_sleeps_and_wakes(self):
        P = 116
        state = {}
        orig = self.mod.Hw.screen
        def spy(s, on):
            orig(s, on); state[round(self.t[0] - 1000, 1)] = (on, self.bl(), os.path.exists(self.mod.SCREENOFF))
        with mock.patch.object(self.mod.Hw, "screen", spy):
            self.run_script([(0.5, [(1, P, 1)]), (0.7, [(1, P, 0)]), (3.0, [(1, P, 1)]), (3.2, [(1, P, 0)])])
        (off_t, off), (on_t, on) = sorted(state.items())
        self.assertEqual(off, (False, 0, True)); self.assertEqual(on, (True, 178, False))
        self.assertEqual(self.poweroffs, [])

    def test_long_power_press_powers_off_once_and_not_screen(self):
        P = 116
        self.run_script([(0.5, [(1, P, 1)]), (3.0, [(1, P, 0)])])
        self.assertEqual(self.poweroffs, ["power key held"])
        self.assertEqual(self.bl(), 178)                       # the release after the long press does NOT toggle the screen

    def test_volume_ignored_while_screen_off(self):
        SEL, UP = 0x13a, 115
        self.run_script([(0.2, [(1, 116, 1)]), (0.3, [(1, 116, 0)]), (0.6, [(1, SEL, 1)]), (0.8, [(1, UP, 1)])])
        self.assertEqual(rdf(self.d.name, "lib/brightness"), "70")        # still the startup value: SELECT+VOL did nothing in the dark

    def test_battery_low_warnings(self):
        hw = self.mod.Hw(self.conf)
        for pct in (50, 15, 14, 10, 9, 5, 4):
            mk(self.sys, "class/power_supply/bat/capacity", pct); hw.battery_tick()
        self.assertEqual(self.msgs, ["Battery low: 15%", "Battery low: 10%", "Battery low: 5%"])
        self.assertTrue(os.path.exists(self.mod.LOWFLAG))
        self.assertEqual(rdf(self.d.name, "run/battery").split()[:2], ["4", "Discharging"])
        mk(self.sys, "class/power_supply/bat/status", "Charging"); hw.battery_tick()
        self.assertFalse(os.path.exists(self.mod.LOWFLAG))     # plugged in: warning cleared

    def test_critical_needs_two_discharging_reads_and_grace(self):
        hw = self.mod.Hw(self.conf)
        mk(self.sys, "class/power_supply/bat/capacity", 2)
        hw.battery_tick(); self.assertEqual(self.poweroffs, []); self.assertIsNone(hw.crit_since)   # one glitchy read is not enough
        hw.battery_tick(); self.assertIsNotNone(hw.crit_since)
        self.assertTrue(any("shutting down" in m for m in self.msgs))
        self.t[0] += 29; hw.battery_tick(); self.assertEqual(self.poweroffs, [])
        self.t[0] += 2;  hw.battery_tick(); self.assertEqual(len(self.poweroffs), 1)
        self.t[0] += 10; hw.battery_tick(); self.assertEqual(len(self.poweroffs), 1)                 # not repeated

    def test_critical_cancelled_by_charger(self):
        hw = self.mod.Hw(self.conf)
        mk(self.sys, "class/power_supply/bat/capacity", 2)
        hw.battery_tick(); hw.battery_tick(); self.assertIsNotNone(hw.crit_since)
        mk(self.sys, "class/power_supply/bat/status", "Charging")
        hw.battery_tick(); self.assertIsNone(hw.crit_since)
        self.t[0] += 100; hw.battery_tick(); self.assertEqual(self.poweroffs, [])

    def test_critical_ignored_when_status_unknown(self):
        hw = self.mod.Hw(self.conf)
        mk(self.sys, "class/power_supply/bat/capacity", 0); mk(self.sys, "class/power_supply/bat/status", "Unknown")
        for _ in range(5):
            hw.battery_tick()
        self.t[0] += 500; hw.battery_tick()
        self.assertEqual(self.poweroffs, [])

    def test_no_config_exits_immediately(self):
        self.conf = None
        with mock.patch.object(hwctl, "load_conf", lambda *a, **k: None):
            self.assertEqual(self.mod.daemon(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
