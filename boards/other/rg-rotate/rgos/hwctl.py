"""hwctl - hardware helpers shared by rgos-hw, rgos-volume, rgos-input (rumble) and rgos-poweroff.

Everything here is *discovery based*: the TrimUI Smart Pro runs the stock 4.9 BSP kernel and nobody has looked at its
sysfs on real hardware yet, so each helper tries the usual nodes in order, uses the first that exists, and reports what
it found (rgos-hwprobe writes that to /var/log/rgos-hw.log). Nothing here raises for a missing node: a missing
backlight/battery/motor just means that feature is off.

Config: /etc/rgos/hw-<device>.json (shipped; <device> = /etc/rgos-device), merged with /var/lib/rgos/hw.json (the
user's own overrides, kept across firmware updates). If no shipped file exists, rgos-hw still provides generic power-key suspend; optional hardware helpers no-op when absent.
"""
import glob, json, os, re, subprocess, time

DEVFILE = "/etc/rgos-device"
USER_CONF = "/var/lib/rgos/hw.json"
SYSFS_GPIO = "/sys/class/gpio"


def device():
    try:
        return open(DEVFILE).read().strip() or "rg34xx-sp"
    except OSError:
        return "rg34xx-sp"


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_conf(path=None, user=USER_CONF):
    """Shipped per-device config merged with the user's overrides; None if this device has no hardware config."""
    path = path or "/etc/rgos/hw-%s.json" % device()
    try:
        with open(path) as f:
            conf = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        with open(user) as f:
            u = json.load(f)
        if isinstance(u, dict):
            conf = _merge(conf, u)
    except (OSError, ValueError):
        pass
    return conf if isinstance(conf, dict) else None


def rd(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def wr(path, text):
    try:
        with open(path, "w") as f:
            f.write(str(text))
        return True
    except OSError:
        return False


def write_run(path, text):
    """Atomic small status file under /run/rgos (the menu polls these)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            f.write(text)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------ backlight

class LowPower:
    """Turn everything down while the device sleeps and put it back exactly as it was afterwards.

    enter(full=False)  radios (Wi-Fi, Bluetooth) off and LEDs dark: used before a real kernel suspend
    enter(full=True)   the above plus CPU at its lowest speed with the extra cores offline and GPU/DRAM devfreq at
                       powersave: used for the software sleep (screen off, game paused) on kernels that cannot suspend
    leave()            restores every value that was changed (only values this class changed; a radio that was already
                       blocked stays blocked)
    The old values are kept in /run/rgos/lowpower.json, so a restarted rgos-hw can still undo a sleep (restore_stale()).
    /run/rgos/sleeping exists while asleep: the Wi-Fi daemon keeps the radio down and the input hub idles.
    Tunables (hw config "low_power"): enabled, radios_off, leds_off, cpu_min, offline_cpus, devfreq_min (all true by default).
    Every write is best-effort: a missing knob is skipped, never an error."""
    DEFAULTS = {"enabled": True, "radios_off": True, "leds_off": True, "cpu_min": True, "offline_cpus": True,
                "devfreq_min": True}
    ORDER = ("offline", "cpu", "devfreq", "led", "radio")        # order of restoring (extra cores back first)

    def __init__(self, conf=None, sys_root="/sys", run_dir="/run/rgos"):
        self.c = dict(self.DEFAULTS)
        extra = (conf or {}).get("low_power", {}) if isinstance(conf, dict) else {}
        if isinstance(extra, dict):
            self.c.update({k: bool(v) for k, v in extra.items() if k in self.DEFAULTS})
        self.sys, self.run_dir = sys_root, run_dir
        self.flag = os.path.join(run_dir, "sleeping")
        self.saved_file = os.path.join(run_dir, "lowpower.json")
        self.saved = {k: [] for k in self.ORDER}
        self.stage = 0                                     # 0 awake, 1 radios + LEDs off, 2 fully turned down

    # ---- helpers
    def _p(self, *parts):
        return os.path.join(self.sys, *parts)

    def _set(self, category, path, value):
        """Write value to a sysfs knob and remember the old value (only when it really changed)."""
        old = rd(path, None)
        if old is None or old == str(value):
            return False
        if not wr(path, value):
            return False
        self.saved[category].append([path, old])
        return True

    def _persist(self):
        write_run(self.saved_file, json.dumps({"stage": self.stage, "saved": self.saved}))

    # ---- stages
    def _light(self):
        if self.c["radios_off"]:
            for d in sorted(glob.glob(self._p("class/rfkill/rfkill*"))):
                if rd(os.path.join(d, "type")) in ("wlan", "bluetooth"):
                    self._set("radio", os.path.join(d, "soft"), 1)
            write_run(self.flag, "1")                      # tells the Wi-Fi daemon to keep the radio down
        if self.c["leds_off"]:
            for d in sorted(glob.glob(self._p("class/leds/*"))):
                if rd(os.path.join(d, "brightness")) not in ("", "0"):
                    self._set("led", os.path.join(d, "brightness"), 0)

    def _deep(self):
        if self.c["cpu_min"]:
            for d in sorted(glob.glob(self._p("devices/system/cpu/cpu[0-9]*/cpufreq"))):
                if "powersave" in rd(os.path.join(d, "scaling_available_governors")).split():
                    self._set("cpu", os.path.join(d, "scaling_governor"), "powersave")
                lowest = rd(os.path.join(d, "cpuinfo_min_freq"))
                if lowest.isdigit():
                    self._set("cpu", os.path.join(d, "scaling_max_freq"), lowest)
        if self.c["devfreq_min"]:
            for d in sorted(glob.glob(self._p("class/devfreq/*"))):
                if "powersave" in rd(os.path.join(d, "available_governors")).split():
                    self._set("devfreq", os.path.join(d, "governor"), "powersave")
        if self.c["offline_cpus"]:                         # last: the cores' cpufreq knobs vanish once they are offline
            def cpu_no(d):
                tail = os.path.basename(d)[3:]
                return int(tail) if tail.isdigit() else -1
            for d in sorted(glob.glob(self._p("devices/system/cpu/cpu[0-9]*")), key=cpu_no):
                if cpu_no(d) >= 1:                         # cpu0 can never go offline
                    self._set("offline", os.path.join(d, "online"), 0)

    def enter(self, full=False):
        if not self.c["enabled"]:
            return
        target = 2 if full else 1
        if self.stage < 1:
            self._light()
            self.stage = 1
        if target >= 2 and self.stage < 2:
            self._deep()
            self.stage = 2
        self._persist()

    def leave(self):
        for cat in self.ORDER:
            for path, old in reversed(self.saved[cat]):
                wr(path, old)
            self.saved[cat] = []
        for p in (self.flag, self.saved_file, self.saved_file + ".tmp"):
            try:
                os.unlink(p)
            except OSError:
                pass
        self.stage = 0

    def restore_stale(self):
        """Undo a sleep that a previous rgos-hw never finished (crash, restart) and drop a stale /run/rgos/sleeping."""
        try:
            with open(self.saved_file) as f:
                data = json.load(f)
            saved = data.get("saved", {})
            for cat in self.ORDER:
                self.saved[cat] = [list(x) for x in saved.get(cat, []) if isinstance(x, list) and len(x) == 2]
        except (OSError, ValueError, AttributeError):
            pass
        self.leave()

    def prefer_deep_sleep(self):
        """Pick the kernel's deepest suspend state (\"deep\" = suspend to RAM) over s2idle when it offers both."""
        path = self._p("power/mem_sleep")
        states = rd(path).split()
        if "deep" in states and "[deep]" not in states:
            return wr(path, "deep")
        return False


class Backlight:
    """Panel backlight as a 0..100 percentage.

    Backends, first match wins (conf["backlight"]["path"] forces one sysfs directory; "backend": "sysfs"|"dispdbg"):
      sysfs    /sys/class/backlight/<x>/{brightness,max_brightness}   (mainline-style, PWM backlight)
      dispdbg  /sys/kernel/debug/dispdbg/{name,command,param,start}   (Allwinner BSP disp2: 'lcd0' 'setbl' <0-255>)
    """
    DISPDBG = "/sys/kernel/debug/dispdbg"

    def __init__(self, conf=None, sys_root="/sys"):
        c = (conf or {}).get("backlight", {}) if conf else {}
        self.min_pct = int(c.get("min_pct", 5))
        self.gamma = float(c.get("gamma", 1.0))
        self.sys_root = sys_root
        self.kind = self.node = None
        self.max = 255
        self.cur = None            # last raw value we wrote (dispdbg cannot be read back reliably)
        want = c.get("backend", "auto")
        if c.get("path"):
            self._use_sysfs(c["path"])
        elif want in ("auto", "sysfs"):
            cands = sorted(glob.glob(os.path.join(sys_root, "class/backlight/*")),
                           key=lambda p: ("acpi" in p, "panel" not in p and "backlight" not in os.path.basename(p), p))
            for p in cands:
                if os.access(os.path.join(p, "brightness"), os.W_OK) and rd(os.path.join(p, "max_brightness")).isdigit():
                    self._use_sysfs(p)
                    break
        if not self.kind and want in ("auto", "dispdbg"):
            d = self.DISPDBG if sys_root == "/sys" else os.path.join(sys_root, "kernel/debug/dispdbg")
            if os.path.exists(os.path.join(d, "command")):
                self.kind, self.node, self.max = "dispdbg", d, int(c.get("dispdbg_max", 255))

    def _use_sysfs(self, p):
        mx = rd(os.path.join(p, "max_brightness"))
        self.kind, self.node = "sysfs", p
        self.max = int(mx) if mx.isdigit() and int(mx) > 0 else 255

    @property
    def available(self):
        return self.kind is not None

    def describe(self):
        return "none" if not self.kind else "%s %s (max %d)" % (self.kind, self.node, self.max)

    def _raw(self, pct):
        pct = max(0, min(100, pct))
        if pct == 0:
            return 0
        return max(1, int(round(self.max * ((pct / 100.0) ** self.gamma))))

    def _pct(self, raw):
        if raw <= 0:
            return 0
        return max(1, min(100, int(round(100.0 * ((raw / float(self.max)) ** (1.0 / self.gamma))))))

    def get(self):
        """Current percentage, or None if unknown."""
        if self.kind == "sysfs":
            v = rd(os.path.join(self.node, "brightness"))
            return self._pct(int(v)) if v.isdigit() else None
        if self.kind == "dispdbg":
            return self._pct(self.cur) if self.cur is not None else None
        return None

    def set(self, pct):
        """Set 0..100 (0 = panel off; anything above 0 is raised to min_pct so the screen never goes accidentally dark)."""
        if not self.kind:
            return False
        pct = 0 if pct <= 0 else max(self.min_pct, min(100, int(pct)))
        raw = self._raw(pct)
        if self.kind == "sysfs":
            ok = wr(os.path.join(self.node, "brightness"), raw)
        else:
            ok = (wr(os.path.join(self.node, "name"), "lcd0") and wr(os.path.join(self.node, "command"), "setbl")
                  and wr(os.path.join(self.node, "param"), raw) and wr(os.path.join(self.node, "start"), 1))
        if ok:
            self.cur = raw
        return ok


# ------------------------------------------------------------------------------------------------ battery
class Battery:
    """First power_supply of type Battery (conf["battery"]["supply"] = a name picks one). read() -> dict or None."""

    def __init__(self, conf=None, sys_root="/sys"):
        c = (conf or {}).get("battery", {}) if conf else {}
        self.vmin = float(c.get("empty_v", 3.3))
        self.vmax = float(c.get("full_v", 4.15))
        self.node = None
        base = os.path.join(sys_root, "class/power_supply")
        want = c.get("supply", "auto")
        cands = sorted(glob.glob(os.path.join(base, "*")))
        if want != "auto":
            cands = [p for p in cands if os.path.basename(p) == want] or cands
        batt = [p for p in cands if rd(os.path.join(p, "type")).lower() == "battery"]
        batt.sort(key=lambda p: "batt" not in os.path.basename(p).lower())
        if batt:
            self.node = batt[0]

    @property
    def available(self):
        return self.node is not None

    def read(self):
        """{"pct", "status", "estimated", "charging", "plugged", "discharging"} or None."""
        if not self.node:
            return None
        cap, est = rd(os.path.join(self.node, "capacity")), False
        pct = int(cap) if cap.lstrip("-").isdigit() else None
        if pct is None:                       # no gauge percentage: estimate from the cell voltage (rough, linear)
            uv = rd(os.path.join(self.node, "voltage_now"))
            if uv.isdigit():
                v = int(uv) / 1e6
                pct, est = int(round(100 * (v - self.vmin) / (self.vmax - self.vmin))), True
        if pct is None:
            return None
        pct = max(0, min(100, pct))
        status = rd(os.path.join(self.node, "status"), "Unknown")
        st = status.lower()
        # "plugged" = on external power (also true for Full / Not charging); only an explicit Discharging counts as running on battery
        return {"pct": pct, "status": status, "estimated": est, "charging": st == "charging",
                "plugged": st in ("charging", "full", "not charging"), "discharging": st == "discharging"}


# ------------------------------------------------------------------------------------------------ audio
def _run(args, timeout=5):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def audio_card(cards_text=None):
    """ALSA card index to use: the first card that is not HDMI/digital-out; 0 when there is only one or none."""
    text = cards_text if cards_text is not None else rd("/proc/asound/cards")
    cards = re.findall(r"^\s*(\d+)\s+\[([^\]]*)\]\s*:\s*(.*)$", text, re.M)
    for idx, cid, desc in cards:
        if not re.search(r"hdmi|spdif|dummy|loopback", cid + " " + desc, re.I):
            return int(idx)
    return int(cards[0][0]) if cards else 0


OUTPUT_RE = re.compile(r"speaker|spk|lineout|line out|headphone|headset|\bhp\b|master|playback|digital|pa\b|amp", re.I)


def audio_enable(card, extra=()):
    """Switch on playback *switches* (mute/amp/output enables) that the codec leaves off after boot. Volume levels are
    rgos-volume's job. Returns the control names it switched on."""
    names = re.findall(r"Simple mixer control '([^']+)',0", _run(["amixer", "-c", str(card), "scontrols"]))
    done = []
    for n in names:
        if "capture" in n.lower() or "mic" in n.lower() or "adc" in n.lower():
            continue
        if not (OUTPUT_RE.search(n) or n in extra):
            continue
        if "pswitch" in _run(["amixer", "-c", str(card), "sget", n]).lower():
            _run(["amixer", "-c", str(card), "-q", "sset", n, "unmute"])
            done.append(n)
    return done


# ------------------------------------------------------------------------------------------------ vibration motor
class Motor:
    """On/off vibration motor. Modes (conf["rumble"]["mode"], default auto):
      gpio    conf["rumble"]["gpio"] = global GPIO number (A133: PH3 = 7*32+3 = 227), "active_low" optional; sysfs gpio
      timed   /sys/class/timed_output/vibrator/enable (Android style: write milliseconds)
      led     /sys/class/leds/<vibrator|motor|rumble>/brightness
      none
    auto = gpio if a number is configured, else timed, else led, else none."""

    def __init__(self, conf=None, sys_root="/sys", gpio_root=None):
        c = (conf or {}).get("rumble", {}) if conf else {}
        self.gpio_root = gpio_root or os.path.join(sys_root, "class/gpio")
        self.active_low = bool(c.get("active_low", False))
        self.mode, self.path, self.state = "none", None, False
        want = c.get("mode", "auto")
        gpio = c.get("gpio")
        timed = os.path.join(sys_root, "class/timed_output/vibrator/enable")
        leds = [p for p in glob.glob(os.path.join(sys_root, "class/leds/*"))
                if re.search(r"vibr|motor|rumble", os.path.basename(p), re.I) and os.path.exists(p + "/brightness")]
        if want in ("auto", "gpio") and gpio not in (None, "", False):
            self.mode, self.path = "gpio", int(gpio)
            self._gpio_prepare()
        elif want in ("auto", "timed") and os.path.exists(timed):
            self.mode, self.path = "timed", timed
        elif want in ("auto", "led") and leds:
            self.mode, self.path = "led", leds[0] + "/brightness"
        if self.mode == "gpio" and not self.path_ok():
            self.mode, self.path = "none", None

    def path_ok(self):
        return os.path.exists(os.path.join(self.gpio_root, "gpio%d" % self.path, "value"))

    def _gpio_prepare(self):
        d = os.path.join(self.gpio_root, "gpio%d" % self.path)
        if not os.path.exists(d):
            wr(os.path.join(self.gpio_root, "export"), self.path)
        # direction "high"/"low" sets the initial level atomically, so the motor never twitches on export
        wr(os.path.join(d, "direction"), "high" if self.active_low else "low")

    @property
    def available(self):
        return self.mode != "none"

    def set(self, on):
        on = bool(on)
        if self.mode == "gpio":
            wr(os.path.join(self.gpio_root, "gpio%d" % self.path, "value"), 1 if on != self.active_low else 0)
        elif self.mode == "timed":
            wr(self.path, 5000 if on else 0)     # the hub switches it off itself; 5 s is only a safety net
        elif self.mode == "led":
            wr(self.path, 1 if on else 0)
        self.state = on

    def pulse(self, ms=120):
        """Short blocking buzz (UI feedback only: keep it under ~250 ms)."""
        if not self.available:
            return
        self.set(True)
        time.sleep(max(0.0, ms) / 1000.0)
        self.set(False)

    def describe(self):
        return "none" if self.mode == "none" else "%s %s" % (self.mode, self.path)


# ------------------------------------------------------------------------------------------------ in-game message
def retroarch_msg(text, port=55355):
    """On-screen message inside a running RetroArch (network_cmd_enable); harmless when it is not running."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(("SHOW_MSG " + text).encode(), ("127.0.0.1", port))
        s.close()
    except OSError:
        pass


def report(conf=None):
    """Plain-text summary of what was discovered (rgos-hwprobe appends it to the log)."""
    conf = conf if conf is not None else load_conf()
    bl, bt, mo = Backlight(conf), Battery(conf), Motor(conf)
    card = audio_card()
    return "\n".join([
        "config: %s" % ("none (this device has no hw-%s.json)" % device() if conf is None else "loaded"),
        "backlight: %s, now %s" % (bl.describe(), bl.get()),
        "battery: %s, now %s" % (bt.node or "none", bt.read()),
        "audio card: %d" % card,
        "motor: %s" % mo.describe(),
    ])
