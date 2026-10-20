#!/usr/bin/env python3
"""RG OS menu - original frontend for the Anbernic RG34XX-SP.

Lists the native Linux ports in <ROMS>/roms/ports/ (the .sh launchers) and on selection writes the script path to
/run/rgos/launch and exits with code 10 so the session loop can run it.
Exit code 20 = power off.
"""
import io, json, math, os, re, subprocess, sys, threading, time
import pygame
from evpad import EvPads, CODES as EV_CODES, EV_KEY, EV_ABS
import fbout
import splash
import wifictl
try:
    import sdcard                      # second SD card (optional: the menu works without it)
except Exception:
    sdcard = None

ROMS_ROOT = os.environ.get("RGOS_ROMS", "/media/roms")
# (folder name, short tag, runner, accepted extensions, full name)
# Ports: native Linux games started by a launcher script (roms/ports/<Name>.sh, data in a sub-folder); rgos-session runs the script itself.
SYSTEMS = [("gba", "GBA", "retroarch", (".gba",), "Game Boy Advance"), ("ports", "PORT", "native", (".sh",), "Ports")]
ROMS_DIR = os.path.join(ROMS_ROOT, "roms")
RUN_DIR = os.environ.get("RGOS_RUN", "/run/rgos")
STATE_FILE = os.environ.get("RGOS_STATE", "/var/lib/rgos/state.json")
SPLASH_DONE = os.path.join(RUN_DIR, "splash-done")      # exists once the startup splash has played (/run is cleared at every boot)
INPUT_CONF = os.environ.get("RGOS_INPUT", "/etc/rgos/input.json")
EXIT_LAUNCH, EXIT_POWER = 10, 20
def _device():
    try:
        with open("/etc/rgos-device") as f:
            return f.read().strip()
    except OSError:
        return ""


# Canvas size: 720x480 (3:2), the RG34XX-SP's panel. RGOS_DEVICE only changes the device id shown in Settings > Information.
DEVICE_ID = os.environ.get("RGOS_DEVICE") or _device() or "rg34xx-sp"
W, H = 720, 480
DX = 0                                         # horizontal layout offset (kept so every screen's coordinates stay as they were)
HAS_STICKS = True

# Colour themes (Settings > Theme). Every screen draws with the module-level BG / PANEL / SEL_BG / ACCENT / TEXT / DIM below;
# apply_theme() swaps them, so the whole menu changes at once. BG = screen, PANEL = header and cards, SEL_BG = selected row,
# ACCENT = highlight, TEXT = main text, DIM = secondary text. The choice is kept in state.json ("theme").
THEMES = [
    ("midnight", "Midnight", dict(BG=(13, 15, 26), PANEL=(24, 28, 46), SEL_BG=(44, 50, 82), ACCENT=(255, 99, 71), TEXT=(232, 235, 245), DIM=(130, 138, 165))),
    ("ocean", "Ocean", dict(BG=(7, 19, 31), PANEL=(13, 35, 55), SEL_BG=(25, 63, 94), ACCENT=(64, 200, 232), TEXT=(226, 240, 248), DIM=(116, 152, 176))),
    ("forest", "Forest", dict(BG=(10, 21, 15), PANEL=(18, 37, 27), SEL_BG=(33, 66, 47), ACCENT=(126, 217, 120), TEXT=(228, 242, 230), DIM=(122, 152, 132))),
    ("sunset", "Sunset", dict(BG=(28, 13, 22), PANEL=(48, 23, 38), SEL_BG=(84, 40, 56), ACCENT=(255, 154, 62), TEXT=(250, 234, 226), DIM=(176, 130, 128))),
    ("purple", "Purple Haze", dict(BG=(19, 12, 31), PANEL=(35, 22, 57), SEL_BG=(65, 40, 103), ACCENT=(198, 120, 255), TEXT=(238, 230, 250), DIM=(150, 130, 184))),
    ("amber", "Amber Terminal", dict(BG=(0, 0, 0), PANEL=(22, 15, 0), SEL_BG=(54, 38, 0), ACCENT=(255, 176, 0), TEXT=(255, 205, 100), DIM=(158, 114, 38))),
    ("gameboy", "Game Boy", dict(BG=(15, 56, 15), PANEL=(28, 78, 28), SEL_BG=(48, 98, 48), ACCENT=(184, 214, 40), TEXT=(224, 248, 208), DIM=(139, 172, 15))),
    ("paper", "Paper (light)", dict(BG=(238, 235, 227), PANEL=(252, 250, 245), SEL_BG=(213, 220, 238), ACCENT=(208, 62, 38), TEXT=(32, 34, 46), DIM=(108, 112, 128))),
    ("contrast", "High Contrast", dict(BG=(0, 0, 0), PANEL=(28, 28, 28), SEL_BG=(72, 72, 72), ACCENT=(255, 235, 0), TEXT=(255, 255, 255), DIM=(176, 176, 176))),
]
DEFAULT_THEME = THEMES[0][0]
BG = PANEL = SEL_BG = ACCENT = TEXT = DIM = (0, 0, 0)


def theme_index(tid):
    for i, t in enumerate(THEMES):
        if t[0] == tid:
            return i
    return 0


def apply_theme(tid):
    """Make the theme with this id the active colour set (unknown ids fall back to the default) and return the id actually used."""
    global BG, PANEL, SEL_BG, ACCENT, TEXT, DIM
    tid, _name, c = THEMES[theme_index(tid)]
    BG, PANEL, SEL_BG, ACCENT, TEXT, DIM = c["BG"], c["PANEL"], c["SEL_BG"], c["ACCENT"], c["TEXT"], c["DIM"]
    return tid


apply_theme(DEFAULT_THEME)

DEFAULT_INPUT = {"buttons": {"a": 0, "b": 1, "l": 4, "r": 5, "select": 6, "start": 7},
                 "debug": False}
KEYMAP = {pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left",
          pygame.K_RIGHT: "right", pygame.K_RETURN: "a", pygame.K_z: "a",
          pygame.K_ESCAPE: "b", pygame.K_x: "b", pygame.K_q: "l", pygame.K_w: "r",
          pygame.K_PAGEUP: "l", pygame.K_PAGEDOWN: "r", pygame.K_TAB: "select",
          pygame.K_SPACE: "start", pygame.K_c: "x"}
REPEATABLE = {"up", "down", "left", "right", "l", "r"}


def load_json(path, default):
    try:
        with open(path) as f:
            data = json.load(f)
        merged = dict(default)
        merged.update(data)
        return merged
    except Exception:
        return dict(default)


class Input:
    def __init__(self):
        self.conf = load_json(INPUT_CONF, DEFAULT_INPUT)
        self.rev = {v: k for k, v in self.conf["buttons"].items()}
        self.held, self.repeat, self.last_raw, self.stick = set(), {}, "", {}
        self.ev, self.ev_seen = EvPads(), False
        self.joys = []
        for i in range(pygame.joystick.get_count()):
            j = pygame.joystick.Joystick(i)
            j.init()
            self.joys.append(j)

    def _down(self, a, out):
        if a in self.held:
            return
        self.held.add(a)
        out.append(a)
        if a in REPEATABLE:
            self.repeat[a] = time.time() + 0.4

    def _up(self, a):
        self.held.discard(a)
        self.repeat.pop(a, None)

    def _dir(self, neg, pos, v, out):
        for a, active in ((neg, v < 0), (pos, v > 0)):
            if active:
                self._down(a, out)
            else:
                self._up(a)

    def poll(self):
        out = []
        evs = self.ev.read()
        if os.path.exists(SCREENOFF_FILE):         # screen asleep (rgos-hw): buttons pressed in the dark do nothing
            self.held.clear()
            self.repeat.clear()
            evs = []
        for t, c, v in evs:
            if t == EV_KEY and c in EV_CODES:
                self.ev_seen = True
                self.last_raw = "ev 0x%x=%s" % (c, EV_CODES[c])
                if v == 1:
                    self._down(EV_CODES[c], out)
                elif v == 0:
                    self._up(EV_CODES[c])
            elif t == EV_ABS and c in (0x00, 0x01):      # left stick from the rgos-sticks hub (-32767..32767)
                d = 0 if abs(v) < 20000 else (1 if v > 0 else -1)
                if d != self.stick.get(c, 0):
                    self.stick[c] = d
                    if c == 0x00:
                        self._dir("left", "right", d, out)
                    else:
                        self._dir("up", "down", d, out)
            elif t == EV_ABS and c == 0x10:
                self._dir("left", "right", v, out)
            elif t == EV_ABS and c == 0x11:
                self._dir("up", "down", v, out)
        joy_ev = (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP, pygame.JOYHATMOTION, pygame.JOYAXISMOTION)
        for e in pygame.event.get():
            if self.ev_seen and e.type in joy_ev:
                continue
            if e.type == pygame.QUIT:
                out.append("quit")
            elif e.type == pygame.KEYDOWN and e.key in KEYMAP:
                self._down(KEYMAP[e.key], out)
            elif e.type == pygame.KEYUP and e.key in KEYMAP:
                self._up(KEYMAP[e.key])
            elif e.type == pygame.JOYBUTTONDOWN:
                self.last_raw = "button %d" % e.button
                a = self.rev.get(e.button)
                if a:
                    self._down(a, out)
            elif e.type == pygame.JOYBUTTONUP:
                a = self.rev.get(e.button)
                if a:
                    self._up(a)
            elif e.type == pygame.JOYHATMOTION:
                self.last_raw = "hat %s" % (e.value,)
                self._dir("left", "right", e.value[0], out)
                self._dir("down", "up", e.value[1], out)
            elif e.type == pygame.JOYAXISMOTION and e.axis in (0, 1):
                v = 0 if abs(e.value) < 0.6 else e.value
                if e.axis == 0:
                    self._dir("left", "right", v, out)
                else:
                    self._dir("up", "down", v, out)
        now = time.time()
        for a, t in list(self.repeat.items()):
            if now >= t:
                out.append(a)
                self.repeat[a] = now + 0.07
        return out


def pretty(name):
    base = os.path.splitext(name)[0]
    base = re.sub(r"\s*[\(\[].*?[\)\]]", "", base).replace("_", " ").strip()
    return base or os.path.splitext(name)[0]


def rom_roots():
    """[(card number, <ROMS root>)]: card 1 always, card 2 when a second SD card is mounted (see sdcard.py)."""
    roots = [(1, ROMS_ROOT)]
    if sdcard is not None and not os.environ.get("RGOS_NO_CARD2"):
        try:
            roots += [(2, r) for r in sdcard.extra_roots() if r != ROMS_ROOT]
        except Exception:
            pass
    return roots


def scan_roms():
    games = []
    for card, root_dir in rom_roots():
        for sid, tag, core, exts, _name in SYSTEMS:
            folder = os.path.join(root_dir, "roms", sid)
            if not os.path.isdir(folder):
                continue
            for root, dirs, files in os.walk(folder):
                if sid == "ports":          # only the launcher scripts in the top folder; sub-folders hold game data
                    dirs[:] = []
                for f in files:
                    if f.lower().endswith(exts) and not f.startswith("."):
                        p = os.path.join(root, f)
                        try:
                            size = os.path.getsize(p)
                        except OSError:
                            size = 0
                        games.append({"title": pretty(f), "path": p, "size": size, "tag": tag, "core": core, "sid": sid,
                                      "card": card})
    games.sort(key=lambda g: (g["title"].lower(), g["tag"], g.get("card", 1)))
    return games


def visible_systems(games):
    """The SYSTEMS entries that have at least one game (systems without ROMs are not listed)."""
    have = {g["sid"] for g in games}
    return [sy for sy in SYSTEMS if sy[0] in have]


def load_state():
    return load_json(STATE_FILE, {"last": ""})


def save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(st, f)
    except Exception:
        pass


def fit(font, text, width):
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text + "..."


def wrap(font, text, width):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if font.size(t)[0] <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:4]


HOME_ITEMS = [("games", "Games"), ("recents", "Recents"), ("tools", "Tools"), ("settings", "Settings")]
# Tools screen: (id, name, description, launch target written to /run/rgos/launch; rgos-session maps the "@tool:" targets)
TOOLS = [("retroarch", "RetroArch", "Open the RetroArch menu (cores, content, settings)", "@tool:retroarch")]
# rows of Settings, in order (ids)
SETTINGS_ROWS = ["theme", "splash", "calib", "bmap", "wifi", "info"]
RECENT_MAX = 20


def icon_games(c, cx, cy, col, bg):
    pygame.draw.rect(c, col, (cx - 36, cy - 20, 72, 40), border_radius=18)
    pygame.draw.rect(c, bg, (cx - 25, cy - 3, 16, 6), border_radius=2)       # D-pad
    pygame.draw.rect(c, bg, (cx - 20, cy - 8, 6, 16), border_radius=2)
    pygame.draw.circle(c, bg, (cx + 14, cy + 4), 5)                          # buttons
    pygame.draw.circle(c, bg, (cx + 25, cy - 4), 5)


def icon_recents(c, cx, cy, col, bg):
    pygame.draw.circle(c, col, (cx, cy), 27, 4)
    pygame.draw.line(c, col, (cx, cy), (cx, cy - 16), 4)
    pygame.draw.line(c, col, (cx, cy), (cx + 12, cy + 7), 4)
    pygame.draw.circle(c, col, (cx, cy), 4)


def icon_settings(c, cx, cy, col, bg):
    for y, kx in ((cy - 18, cx - 10), (cy, cx + 14), (cy + 18, cx - 2)):    # three sliders
        pygame.draw.line(c, col, (cx - 32, y), (cx + 32, y), 4)
        pygame.draw.circle(c, col, (kx, y), 8)
        pygame.draw.circle(c, bg, (kx, y), 3)


def icon_tools(c, cx, cy, col, bg):
    pygame.draw.rect(c, col, (cx - 34, cy - 6, 68, 12), border_radius=4)                 # wrench handle
    pygame.draw.circle(c, col, (cx - 30, cy), 14)                                         # open jaw
    pygame.draw.circle(c, bg, (cx - 30, cy), 7)
    pygame.draw.rect(c, bg, (cx - 46, cy - 4, 18, 8))
    pygame.draw.circle(c, col, (cx + 34, cy), 6)
    pygame.draw.circle(c, bg, (cx + 34, cy), 2)


ICONS = {"games": icon_games, "recents": icon_recents, "tools": icon_tools, "settings": icon_settings}


def rd_file(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def card2_info():
    """Settings > Information text for the second SD card."""
    st = sdcard.status() if sdcard is not None else None
    if not st:
        return "none"
    u = sdcard.usage(st["mount"])
    return ("%.1f / %.1f GB free" % (u[0] / 1e9, u[1] / 1e9) if u else "mounted") + (" (read-only)" if st.get("ro") else "")


def settings_info():
    """Read-only system info lines for the Settings > Information screen."""
    build = "".join(l[9:] for l in rd_file("/etc/rgos-release").splitlines() if l.startswith("BUILD_ID="))
    try:
        st = os.statvfs(ROMS_ROOT)
        space = "%.1f / %.1f GB free" % (st.f_bavail * st.f_frsize / 1e9, st.f_blocks * st.f_frsize / 1e9)
    except OSError:
        space = "not mounted"
    vol, bat, bri = read_volume(), read_battery(), read_brightness()
    return [("RG OS build", build or "unknown"), ("Device", DEVICE_ID), ("Kernel", os.uname().release),
            ("ROMS partition", space), ("SD card 2", card2_info()), ("Volume", "%d%%" % vol[0] if vol else "n/a"),
            ("Battery", ("%d%% %s" % (bat[0], bat[1].replace("_", " ").lower())) if bat else "n/a"),
            ("Brightness", "%d%%" % bri[0] if bri else "n/a"),
            ] + ([("Sticks", "calibrated" if os.path.exists(STICK_MAP) else "default wiring")] if HAS_STICKS else []) + [
            ("Buttons", "mapped" if os.path.exists(BTN_MAP) else "default codes")]


STICK_MAP = os.environ.get("RGOS_STICKMAP", "/var/lib/rgos/stick-map.json")
STICK_CAL = os.environ.get("RGOS_STICKCAL", "/var/lib/rgos/sticks.json")


class StickCal:
    """Guided stick calibration. The hub (rgos-sticks) publishes the raw ADC value of each of the 4 mux positions while
    the probe flag exists. We ask for one stick direction at a time (left stick left/up, right stick left/up), see which
    mux position moved and which way, and so learn which position is which axis and its polarity. The result goes to
    stick-map.json and the hub is restarted, so it works whatever the real mux wiring is."""
    PROMPTS = [("LEFT stick", "LEFT", 0), ("LEFT stick", "UP", 1), ("RIGHT stick", "LEFT", 2), ("RIGHT stick", "UP", 3)]
    MOVE, REST, HOLD, TIMEOUT = 350, 200, 0.4, 25.0

    def __init__(self):
        self.flag, self.rawf = os.path.join(RUN_DIR, "sticks.probe"), os.path.join(RUN_DIR, "sticks.raw")
        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            os.unlink(self.rawf)
        except OSError:
            pass
        try:
            open(self.flag, "w").close()
        except OSError:
            pass
        self.state, self.step, self.msg = "base", 0, ""
        self.t0 = self.tstate = time.time()
        self.stamp, self.hist, self.base, self.cand, self.cand_t, self.tb = None, [], None, None, 0.0, 0.0
        self.found = {}                                   # axis -> (mux position, inverted)

    def stop(self):
        for f in (self.flag, self.rawf):
            try:
                os.unlink(f)
            except OSError:
                pass

    def _read(self):
        try:
            with open(self.rawf) as f:
                n, *v = f.read().split()
            v = [int(x) for x in v]
            return (n, v) if len(v) == 4 else None
        except (OSError, ValueError):
            return None

    def _fail(self, msg):
        self.state, self.msg = "fail", msg
        self.stop()

    def update(self):
        now = time.time()
        if self.state in ("done", "fail"):
            return
        if self.state == "apply":                         # one frame after "Applying..." was drawn
            inv = [False] * 4
            mp = [0] * 4
            for axis, (pos, flip) in self.found.items():
                mp[pos], inv[axis] = axis, flip
            try:
                os.makedirs(os.path.dirname(STICK_MAP), exist_ok=True)
                with open(STICK_MAP, "w") as f:
                    json.dump({"map": mp, "invert": inv}, f)
                for p in (STICK_CAL,):                    # learned ranges belonged to the old axis order
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                if os.environ.get("RGOS_WINDOWED") != "1":
                    subprocess.run(["systemctl", "restart", "rgos-sticks"], timeout=30)
            except Exception as e:
                return self._fail("Could not save: %s" % e)
            self.stop()
            self.state, self.msg = "done", "Saved. Move the sticks in the menu to test them."
            return
        r = self._read()
        if r is None:
            if now - self.t0 > 5:
                self._fail("The stick hub is not running (no raw values).")
            return
        if r[0] == self.stamp:
            if now - self.tstate > 3 and self.state != "base":
                pass
            return
        self.stamp = r[0]
        self.hist = (self.hist + [r[1]])[-3:]
        cur = [sorted(h[i] for h in self.hist)[len(self.hist) // 2] for i in range(4)]     # median of 3
        if self.state == "base":
            if self.base is None or max(abs(cur[i] - self.base[i]) for i in range(4)) > self.REST:
                self.base, self.tb = cur, now              # (re)start: a stick is being touched or this is the first reading
                return
            if now - self.tb < 1.2:                        # sticks at rest for a moment: remember where each position sits
                return
            self.state, self.tstate = "prompt", now
            return
        dev = [cur[i] - self.base[i] for i in range(4)]
        used = {pos for pos, _ in self.found.values()}
        if self.state == "prompt":
            if now - self.tstate > self.TIMEOUT:
                return self._fail("No stick movement detected.")
            free = [i for i in range(4) if i not in used]
            best = max(free, key=lambda i: abs(dev[i]))
            others = [abs(dev[i]) for i in free if i != best]
            if abs(dev[best]) >= self.MOVE and (not others or max(others) < 0.6 * abs(dev[best])):
                if self.cand != best:
                    self.cand, self.cand_t = best, now
                elif now - self.cand_t >= self.HOLD:
                    self.found[self.PROMPTS[self.step][2]] = (best, dev[best] > 0)
                    self.state, self.tstate, self.cand = "release", now, None
            else:
                self.cand = None
        elif self.state == "release":
            if max(abs(d) for d in dev) < self.REST:
                if self.cand is None:
                    self.cand, self.cand_t = True, now
                elif now - self.cand_t >= self.HOLD:
                    self.cand = None
                    self.step += 1
                    self.state, self.tstate = ("prompt", now) if self.step < len(self.PROMPTS) else ("apply", now)
            else:
                self.cand = None
            if now - self.tstate > self.TIMEOUT:
                return self._fail("Please let go of the sticks and try again.")

    def lines(self):
        if self.state == "base":
            return ["Let go of both sticks", "Reading the resting position..."]
        if self.state == "prompt":
            who, way, _ = self.PROMPTS[self.step]
            return ["Push the %s fully %s" % (who, way), "and hold it there"]
        if self.state == "release":
            return ["Got it. Let go of the stick", "(back to the centre)"]
        if self.state == "apply":
            return ["Applying...", "keep both sticks released"]
        return [self.msg]


BTN_MAP = os.environ.get("RGOS_BTNMAP", "/var/lib/rgos/button-map.json")


class ButtonMap:
    """Guided button mapping. While the probe flag exists the hub (rgos-sticks) publishes the last raw key / hat / trigger
    press of every source device as "<n>\\t<device>\\t<type>\\t<code>\\t<sign>". We ask for one button at a time and
    store raw press -> canonical code in button-map.json, then restart the hub. Optional buttons are skipped when
    nothing is pressed for a few seconds. Do not touch the sticks while this runs."""
    PROMPTS = [("A", 0x131, False), ("B", 0x130, False), ("X", 0x133, False), ("Y", 0x134, False),
               ("L", 0x136, False), ("R", 0x137, False), ("SELECT", 0x13a, False), ("START", 0x13b, False),
               ("D-pad UP", 0x220, False), ("D-pad DOWN", 0x221, False), ("D-pad LEFT", 0x222, False),
               ("D-pad RIGHT", 0x223, False), ("MENU (home) button", 0x13c, True), ("L2 / left trigger", 0x138, True),
               ("R2 / right trigger", 0x139, True), ("L3 (left stick click)", 0x13d, True),
               ("R3 (right stick click)", 0x13e, True)]
    TIMEOUT, OPT_TIMEOUT, GAP = 25.0, 5.0, 0.5
    if not HAS_STICKS:        # (no sticks: no L2/R2 triggers or stick clicks to teach)
        PROMPTS = [p for p in PROMPTS if p[1] not in (0x138, 0x139, 0x13d, 0x13e)]

    def __init__(self):
        self.flag, self.rawf = os.path.join(RUN_DIR, "keys.probe"), os.path.join(RUN_DIR, "keys.raw")
        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            os.unlink(self.rawf)
        except OSError:
            pass
        try:
            open(self.flag, "w").close()
        except OSError:
            pass
        self.state, self.step, self.msg = "wait", 0, ""
        self.t0 = self.tstate = time.time()
        self.stamp, self.keys, self.used, self.last_name = None, {}, set(), ""
        self.skipped = []

    def stop(self):
        for f in (self.flag, self.rawf):
            try:
                os.unlink(f)
            except OSError:
                pass

    def _read(self):
        try:
            with open(self.rawf) as f:
                n, name, t, c, sg = f.read().rstrip("\n").split("\t")
            return n, name, int(t), int(c), int(sg)
        except (OSError, ValueError):
            return None

    def _fail(self, msg):
        self.state, self.msg = "fail", msg
        self.stop()

    def _next(self, now):
        self.step += 1
        self.state, self.tstate = ("prompt", now) if self.step < len(self.PROMPTS) else ("apply", now)

    def update(self):
        now = time.time()
        if self.state in ("done", "fail"):
            return
        if self.state == "apply":
            try:
                os.makedirs(os.path.dirname(BTN_MAP), exist_ok=True)
                with open(BTN_MAP + ".tmp", "w") as f:
                    json.dump({"keys": self.keys}, f)
                os.replace(BTN_MAP + ".tmp", BTN_MAP)
                if os.environ.get("RGOS_WINDOWED") != "1":
                    subprocess.run(["systemctl", "restart", "rgos-sticks"], timeout=30)
            except Exception as e:
                return self._fail("Could not save: %s" % e)
            self.stop()
            self.state = "done"
            self.msg = "Saved %d buttons%s. Press buttons in the menu to test them." % (
                len(self.keys), (" (skipped: %s)" % ", ".join(self.skipped)) if self.skipped else "")
            return
        r = self._read()
        label, canon, optional = self.PROMPTS[min(self.step, len(self.PROMPTS) - 1)]
        if self.state == "wait":                        # give the hub a moment, then start
            if now - self.t0 > 1.0:
                self.state, self.tstate = "prompt", now
                if r:
                    self.stamp = r[0]                   # ignore a press that was already there
            return
        if self.state == "prompt":
            limit = self.OPT_TIMEOUT if optional else self.TIMEOUT
            if now - self.tstate > limit:
                if optional:
                    self.skipped.append(label.split(" ")[0])
                    return self._next(now)
                if r is None and now - self.t0 > 8:
                    return self._fail("The input hub is not running (no key events).")
                return self._fail("No button press detected for %s." % label)
            if r and r[0] != self.stamp and now - self.tstate > self.GAP:
                self.stamp = r[0]
                _, name, t, c, sg = r
                key = "%s|%d:%d:%d" % (name, t, c, sg)
                if key in self.used:
                    return                              # already assigned to another button: press a different one
                self.used.add(key)
                self.keys[key] = canon
                self.last_name = name
                self._next(now)
            elif r and r[0] != self.stamp:
                self.stamp = r[0]

    def lines(self):
        if self.state == "wait":
            return ["Starting...", "Do not touch the sticks" if HAS_STICKS else "Do not press anything yet"]
        if self.state == "prompt":
            label, _, optional = self.PROMPTS[self.step]
            return ["Press %s" % label, "(wait a few seconds to skip: this one is optional)" if optional
                    else "Do not touch the sticks" if HAS_STICKS else ""]
        if self.state == "apply":
            return ["Applying...", ""]
        return [self.msg]


VOLUME_FILE = os.environ.get("RGOS_VOLUME_FILE", os.path.join(RUN_DIR, "volume"))


CALIB_DONE = os.environ.get("RGOS_CALIBDONE", "/var/lib/rgos/calibrated")   # exists once the forced first-run setup was completed


def calibration_needed():
    """True on the first start of a fresh install: the launcher then forces the stick + button calibration.
    An install that already has its calibration files (updated from an older version) is marked done without asking."""
    if os.path.exists(CALIB_DONE):
        return False
    if os.path.exists(BTN_MAP) and (not HAS_STICKS or os.path.exists(STICK_MAP)):
        mark_calibrated()
        return False
    return True


def mark_calibrated():
    try:
        os.makedirs(os.path.dirname(CALIB_DONE), exist_ok=True)
        open(CALIB_DONE, "w").close()
    except OSError as e:
        log("could not write %s: %r" % (CALIB_DONE, e))


def read_volume():
    """(percent, stamp) written by rgos-volume, or None if the daemon has not run."""
    try:
        with open(VOLUME_FILE) as f:
            v, stamp = f.read().split()[:2]
        return int(v), stamp
    except (OSError, ValueError):
        return None


BRIGHT_FILE = os.environ.get("RGOS_BRIGHT_FILE", os.path.join(RUN_DIR, "brightness"))
BATT_FILE = os.environ.get("RGOS_BATT_FILE", os.path.join(RUN_DIR, "battery"))
SCREENOFF_FILE = os.path.join(RUN_DIR, "screenoff")


def read_brightness():
    """(percent, stamp) written by rgos-hw (TrimUI Smart Pro), or None."""
    try:
        with open(BRIGHT_FILE) as f:
            v, stamp = f.read().split()[:2]
        return int(v), stamp
    except (OSError, ValueError):
        return None


def read_battery():
    """(percent, status) written by rgos-hw, or None when there is no gauge / the daemon is not running."""
    try:
        with open(BATT_FILE) as f:
            pct, status, stamp = f.read().split()[:3]
        if time.time() - int(stamp) > 60:         # stale: the daemon died
            return None
        return int(pct), status
    except (OSError, ValueError):
        return None


KB_LAYERS = [["1234567890", "qwertyuiop", "asdfghjkl-", "zxcvbnm._@"],
             ["1234567890", "QWERTYUIOP", "ASDFGHJKL-", "ZXCVBNM._@"],
             ["!@#$%^&*()", "-_=+[]{};:", "'\",<>/?\\|`", "~.@"]]
KB_LAYER_NAMES = ["abc", "ABC", "#+="]
KB_BOTTOM = ["Layer", "Space", "Del", "OK", "Cancel"]


class Kbd:
    """On-screen keyboard state: a 4-row character grid plus a bottom row of Layer/Space/Del/OK/Cancel."""
    def __init__(self, field, title, text, secret):
        self.field, self.title, self.text, self.secret = field, title, text, secret
        self.show, self.layer, self.row, self.col = not secret, 0, 1, 0

    def keys(self, row=None):
        r = self.row if row is None else row
        return list(KB_LAYERS[self.layer][r]) if r < 4 else KB_BOTTOM

    def move(self, dr, dc):
        if dr:
            self.row = (self.row + dr) % 5
        if dc:
            self.col = (self.col + dc) % len(self.keys())
        self.col = min(self.col, len(self.keys()) - 1)

    def press(self):
        """Press the key under the cursor; returns 'ok', 'cancel' or None."""
        k = self.keys()[self.col]
        if self.row < 4:
            if len(self.text) < 64:
                self.text += k
        elif k == "Layer":
            self.layer = (self.layer + 1) % 3
        elif k == "Space":
            if len(self.text) < 64:
                self.text += " "
        elif k == "Del":
            self.text = self.text[:-1]
        elif k == "OK":
            return "ok"
        elif k == "Cancel":
            return "cancel"
        return None


def wrap_n(font, text, width, n):
    """Word-wrap into at most n lines; the last one gets '...' if the text did not fit."""
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if font.size(t)[0] <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > n:
        lines = lines[:n]
        lines[-1] = fit(font, lines[-1] + " ...", width)
    return lines


def tail_fit(font, text, width):
    """Show the end of text if it is too wide (so the cursor end stays visible)."""
    while text and font.size(text)[0] > width:
        text = text[1:]
    return text


def log(msg):
    sys.stderr.write("%s menu: %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


def main():
    windowed = os.environ.get("RGOS_WINDOWED") == "1"
    # RGOS_VIDEO=fb: use SDL's dummy driver for the off-screen canvas and draw into /dev/fb0 through fbout.
    if not windowed and os.environ.get("RGOS_VIDEO") == "fb":
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.init()
    pygame.joystick.init()
    fb = None
    win = None
    if not windowed and os.environ.get("RGOS_VIDEO") != "fb":
        try:
            win = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            log("video: SDL %s %s" % (pygame.display.get_driver(), win.get_size()))
            win.fill((0, 0, 0))             # first flip right away: wipes whatever the boot console left on the panel
            pygame.display.flip()
        except pygame.error as e:
            log("SDL video failed (%s); falling back to /dev/fb0" % e)
    elif windowed:
        win = pygame.display.set_mode((W, H))
    if win is None:
        # No SDL display: draw into an off-screen surface and push it to the framebuffer ourselves.
        pygame.display.quit()
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.display.init()
        win = pygame.display.set_mode((W, H))
        fb = fbout.FbOut()
        log("video: framebuffer " + fb.info)
    pygame.mouse.set_visible(False)
    if not windowed:
        fbout.set_graphics(True)      # keep the kernel console from drawing over the menu
    canvas = pygame.Surface((W, H))
    fpath = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.exists(fpath):
        mk = lambda s: pygame.font.Font(fpath, s)
    else:
        mk = lambda s: pygame.font.Font(None, int(s * 1.3))
    f_logo, f_big, f_row, f_small, f_tiny = mk(30), mk(22), mk(19), mk(14), mk(11)

    inp = Input()
    all_games = scan_roms()
    games_of = lambda sid: [g for g in all_games if g["sid"] == sid]
    vsys = visible_systems(all_games)
    NO_SYSTEM = ("", "", "", (), "")           # placeholder while no system has any games
    state = load_state()
    theme_id = apply_theme(state.get("theme"))

    def recent_games():
        by_path = {g["path"]: g for g in all_games}
        return [by_path[p] for p in state.get("recent", []) if p in by_path]

    # screens: "home" (Games / Recents / Settings) -> "systems" (consoles) -> "games"; "recents"; "settings"
    view, home_idx, sys_idx, idx_by = "home", 0, 0, {}
    cal = bmap = None
    set_idx, tool_idx, kb = 0, 0, None
    wconf, wscan, wf_idx, wf_msg, wf_msg_until, wf_pending = wifictl.load_conf(), wifictl.Scanner(), 0, "", 0.0, ("", False)

    def wf_do(fn, msg, *args):
        """Run a Wi-Fi change off the UI thread (it talks to wpa_cli), show msg for a few seconds, then reload the saved list."""
        nonlocal wf_msg, wf_msg_until, wconf
        wf_msg, wf_msg_until = msg, time.time() + 4

        def job_():
            nonlocal wconf
            fn(*args)
            wconf = wifictl.load_conf()
        threading.Thread(target=job_, daemon=True).start()
    last = next((g for g in all_games if g["path"] == state.get("last")), None)
    if last:                                   # start with the cursor on the system/game played last
        sys_idx = next(i for i, sy in enumerate(vsys) if sy[0] == last["sid"])
        idx_by[last["sid"]] = games_of(last["sid"]).index(last)
    rows, list_x, list_w, top_y, row_h = 8, 20, 450 + DX, 84, 44
    clock = pygame.time.Clock()
    code = None
    vol_seen = read_volume()
    vol_seen = vol_seen[1] if vol_seen else None
    vol_show_until, vol_pct = 0.0, 0
    bri_seen, bri_show_until, bri_pct = None, 0.0, 0
    _b = read_brightness()
    if _b:
        bri_seen = _b[1]                           # the popup only appears for changes made while the menu runs

    def launch(target, game=None):
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(os.path.join(RUN_DIR, "launch"), "w") as f:
            f.write(target)
        if game:
            state["last"] = game["path"]
            state["recent"] = [game["path"]] + [p for p in state.get("recent", []) if p != game["path"]][:RECENT_MAX - 1]
        save_state(state)
        return EXIT_LAUNCH

    def present():
        """Put the canvas on the screen (framebuffer or SDL window, scaled to fit with black bars if the sizes differ)."""
        if fb is not None:
            fb.present(canvas)
        else:
            if (W, H) == win.get_size():
                win.blit(canvas, (0, 0))
            else:                      # keep the menu undistorted: scale to fit, black bars around it
                fx, fy, fw, fh = fbout.fit_rect((W, H), win.get_size())
                if (fw, fh) != win.get_size():
                    win.fill((0, 0, 0))     # every frame: SDL may flip between two buffers
                win.blit(pygame.transform.smoothscale(canvas, (fw, fh)), (fx, fy))
            pygame.display.flip()

    def play_splash(mode):
        """Play the startup splash video (see splash.py); any button skips it. mode: "on" (with sound) | "silent"."""
        path = splash.find_splash(DEVICE_ID, ROMS_ROOT)
        if not path:
            log("splash: no video found")
            return
        with open(path, "rb") as f:
            data = f.read()
        frames, conf, snd = splash.split_mjpeg(data), splash.read_conf(path), None
        log("splash: %s, %d frames at %g fps" % (path, len(frames), conf["fps"]))
        if mode == "on" and conf["sound"]:
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init(22050, -16, 1, 1024)
                snd = pygame.mixer.Sound(conf["sound"])
                snd.play()
            except Exception as e:             # no sound card / busy device: the video still plays
                log("splash: no sound (%s)" % e)
                snd = None

        def decode(b):
            return pygame.image.load(io.BytesIO(b), "frame.jpg").convert()

        def show(img):
            iw, ih = img.get_size()
            if (iw, ih) == (W, H):
                canvas.blit(img, (0, 0))
            else:                              # a video of another size (your own file): fit it, black bars around it
                sc = min(W / iw, H / ih)
                sw, sh = max(1, int(iw * sc)), max(1, int(ih * sc))
                canvas.fill((0, 0, 0))
                canvas.blit(pygame.transform.smoothscale(img, (sw, sh)), ((W - sw) // 2, (H - sh) // 2))
            present()

        inp.poll()                             # drop buttons pressed during boot, then any new press skips
        n = splash.play(data, frames, conf["fps"], decode, show, lambda: bool(inp.poll()))
        log("splash: %d of %d frames shown" % (n, len(frames)))
        if snd:
            snd.stop()
        if pygame.mixer.get_init():
            pygame.mixer.quit()
        canvas.fill(BG)
        present()

    if os.environ.get("RGOS_SPLASH") == "always" or not os.path.exists(SPLASH_DONE):
        try:                                   # mark it first: the menu restarts after every game and must not replay it
            os.makedirs(RUN_DIR, exist_ok=True)
            open(SPLASH_DONE, "w").close()
        except OSError:
            pass
        if splash.mode_of(state) != "off":
            try:
                play_splash(splash.mode_of(state))
            except Exception as e:
                log("splash failed: %r" % (e,))

    # First start of a fresh install: force the guided stick calibration, then the button mapping, before the launcher
    # can be used. A failed step restarts by itself; after 3 failures MENU may skip that step (so a broken stick hub can
    # never lock the device out). The marker file is written only when the whole setup is finished.
    forced = calibration_needed()
    forced_stage, forced_fails, forced_t = "calib" if HAS_STICKS else "bmap", 0, None
    if forced:
        if forced_stage == "calib":
            cal, view = StickCal(), "calib"
        else:
            bmap, view = ButtonMap(), "bmap"

    def forced_next():
        nonlocal forced, forced_stage, forced_fails, forced_t, bmap, view
        forced_fails, forced_t = 0, None
        if forced_stage == "calib":
            forced_stage, bmap, view = "bmap", ButtonMap(), "bmap"
        else:
            forced, view = False, "home"
            mark_calibrated()

    while code is None:
        for a in inp.poll():
            if forced:                         # first-run setup: buttons only feed the wizard (and MENU skips after 3 failures)
                if a == "quit":
                    code = EXIT_POWER
                elif a == "menu" and forced_fails >= 3:
                    (cal if forced_stage == "calib" else bmap).stop()
                    forced_next()
                continue
            if view in ("games", "recents"):
                key = "\0recents" if view == "recents" else (vsys[sys_idx][0] if vsys else "")
                games = recent_games() if view == "recents" else games_of(vsys[sys_idx][0] if vsys else "")
                n, idx = len(games), idx_by.get(key, 0)
            if a == "quit":
                code = EXIT_POWER
            elif a == "menu":                  # MENU / home button: back to the home screen
                if view in ("systems", "games", "recents", "tools", "settings", "wifi", "info"):
                    view = "home"
                elif view == "calib" and cal and cal.state in ("done", "fail"):
                    view = "home"
                elif view == "bmap" and bmap and bmap.state in ("done", "fail"):
                    view = "home"
            elif a == "x" and view in ("home", "systems", "games", "recents"):
                cur_sid = vsys[sys_idx][0] if vsys else None
                all_games = scan_roms()        # rescan
                vsys = visible_systems(all_games)
                sys_idx = next((i for i, sy in enumerate(vsys) if sy[0] == cur_sid), min(sys_idx, max(0, len(vsys) - 1)))
                if view == "games" and not any(sy[0] == cur_sid for sy in vsys):
                    view = "systems"           # the system lost its last game
                elif view in ("games", "recents"):
                    n = len(recent_games() if view == "recents" else games_of(vsys[sys_idx][0] if vsys else ""))
                    idx_by[key] = min(idx, max(0, n - 1))
            elif view == "home":               # 2x2 grid with three boxes: left/right change column, up/down change row
                col, row = home_idx % 2, home_idx // 2
                nxt = home_idx
                if a in ("left", "right"):
                    nxt = row * 2 + (1 - col)
                elif a in ("up", "down"):
                    nxt = (1 - row) * 2 + col
                if a in ("left", "right", "up", "down"):
                    home_idx = nxt if nxt < len(HOME_ITEMS) else (len(HOME_ITEMS) - 1 if a in ("up", "down") else home_idx)
                elif a == "a":
                    view = HOME_ITEMS[home_idx][0]
                    if view == "games":
                        view = "systems"       # Games -> list of consoles (Game Boy Advance, Ports, ...) -> games
                        sys_idx = min(sys_idx, max(0, len(vsys) - 1))
            elif view == "systems":
                if vsys and a in ("down", "right"):
                    sys_idx = (sys_idx + 1) % len(vsys)
                elif vsys and a in ("up", "left"):
                    sys_idx = (sys_idx - 1) % len(vsys)
                elif a == "a" and vsys:
                    view = "games"
                elif a == "b":
                    view = "home"
            elif view in ("games", "recents"):
                if a == "b":
                    view = "systems" if view == "games" else "home"
                elif n and a == "down":
                    idx_by[key] = (idx + 1) % n
                elif n and a == "up":
                    idx_by[key] = (idx - 1) % n
                elif n and a in ("r", "right"):
                    idx_by[key] = min(n - 1, idx + rows)
                elif n and a in ("l", "left"):
                    idx_by[key] = max(0, idx - rows)
                elif a == "a" and n:
                    code = launch(games[idx]["path"], games[idx])
            elif view == "tools":
                if a in ("up", "down"):
                    tool_idx = (tool_idx + (1 if a == "down" else -1)) % len(TOOLS)
                elif a == "b":
                    view = "home"
                elif a == "a":
                    code = launch(TOOLS[tool_idx][3])
            elif view == "settings":
                if a in ("up", "down"):
                    set_idx = (set_idx + (1 if a == "down" else -1)) % len(SETTINGS_ROWS)
                elif a in ("left", "right") and SETTINGS_ROWS[set_idx] == "theme":
                    theme_id = apply_theme(THEMES[(theme_index(theme_id) + (1 if a == "right" else -1)) % len(THEMES)][0])
                    state["theme"] = theme_id
                    save_state(state)
                elif a in ("left", "right") and SETTINGS_ROWS[set_idx] == "splash":
                    state["splash"] = splash.MODES[(splash.MODES.index(splash.mode_of(state)) + (1 if a == "right" else -1)) % len(splash.MODES)]
                    save_state(state)
                elif a == "x" and SETTINGS_ROWS[set_idx] == "splash":       # preview now (with sound unless set to silent)
                    play_splash("silent" if splash.mode_of(state) == "silent" else "on")
                elif a == "b":
                    view = "home"
                elif a == "a":
                    row = SETTINGS_ROWS[set_idx]
                    if row == "theme":         # A also steps to the next theme
                        theme_id = apply_theme(THEMES[(theme_index(theme_id) + 1) % len(THEMES)][0])
                        state["theme"] = theme_id
                        save_state(state)
                    elif row == "splash":      # A steps on -> silent -> off
                        state["splash"] = splash.MODES[(splash.MODES.index(splash.mode_of(state)) + 1) % len(splash.MODES)]
                        save_state(state)
                    elif row == "wifi":
                        wconf, wf_idx, wf_msg, wf_msg_until = wifictl.load_conf(), 0, "", 0.0
                        wscan.kick()
                        view = "wifi"
                    elif row == "info":        # read-only system information
                        view = "info"
                    elif row == "calib":       # guided stick calibration
                        cal, view = StickCal(), "calib"
                    else:                      # guided button mapping
                        bmap, view = ButtonMap(), "bmap"
            elif view == "info":
                if a == "b":
                    view = "settings"
            elif view == "kbd":
                if a in ("up", "down", "left", "right"):
                    kb.move({"up": -1, "down": 1}.get(a, 0), {"left": -1, "right": 1}.get(a, 0))
                elif a == "b":
                    kb.text = kb.text[:-1]
                elif a == "x" and kb.secret:
                    kb.show = not kb.show
                elif a in ("a", "start"):
                    res = kb.press() if a == "a" else "ok"
                    if res and kb.field.startswith("wifi"):
                        view = "wifi"
                        if res == "ok" and kb.field == "wifi_ssid":
                            if kb.text.strip():
                                wf_pending = (kb.text, True)
                                kb, view = Kbd("wifi_psk", "Password for %s (empty = open network)" % kb.text, "", True), "kbd"
                        elif res == "ok":
                            ssid_, hidden_ = wf_pending
                            if kb.text == "" and hidden_:
                                wf_do(wifictl.join, "Joining %s..." % ssid_, ssid_, None, True)
                            elif not wifictl.password_ok(kb.text):
                                wf_msg, wf_msg_until = "The password must be 8-63 characters", time.time() + 4
                            else:
                                wf_do(wifictl.join, "Joining %s..." % ssid_, ssid_, kb.text, hidden_)
                    elif res:
                        view = "settings"
            elif view == "wifi":
                wrows = wscan.rows(wconf)
                wn = len(wrows) + 2                       # toggle, networks..., join hidden
                if a == "down":
                    wf_idx = (wf_idx + 1) % wn
                elif a == "up":
                    wf_idx = (wf_idx - 1) % wn
                elif a == "b":
                    view = "settings"
                elif a == "x":
                    wscan.kick()
                elif wf_idx == 0 and a == "a":
                    wifictl.set_enabled(not wconf["enabled"])
                    wconf = wifictl.load_conf()
                    if wconf["enabled"]:
                        wscan.kick()
                elif wf_idx == wn - 1 and a == "a":      # join a hidden network by name
                    kb, view = Kbd("wifi_ssid", "Hidden network name (SSID)", "", False), "kbd"
                elif 0 < wf_idx < wn - 1:
                    net = wrows[wf_idx - 1]
                    if a == "y" and net["saved"]:
                        wf_do(wifictl.forget_and_apply, "Forgot %s" % net["ssid"], net["ssid"])
                    elif a == "a":
                        if net["unsupported"]:
                            wf_msg, wf_msg_until = "WPA3-only and WEP networks are not supported", time.time() + 4
                        elif net["saved"]:
                            wf_do(wifictl.connect, "Connecting to %s..." % net["ssid"], net["ssid"])
                        elif net["secured"]:
                            wf_pending = (net["ssid"], False)
                            kb, view = Kbd("wifi_psk", "Password for %s" % net["ssid"], "", True), "kbd"
                        else:
                            wf_do(wifictl.join, "Joining %s..." % net["ssid"], net["ssid"], None, False)
            elif view == "bmap":
                if bmap.state in ("done", "fail") and a in ("a", "b"):
                    view = "settings"          # (while mapping, buttons only feed the wizard; it times out by itself)
            elif view == "calib":
                if a in ("a", "b"):
                    if cal.state in ("done", "fail"):
                        view = "settings"
                    elif a == "b":             # cancel
                        cal.stop()
                        view = "settings"
        if view == "calib" and cal:
            cal.update()
        if view == "bmap" and bmap:
            bmap.update()
        if forced:
            wiz, now_f = (cal if forced_stage == "calib" else bmap), time.time()
            if wiz.state == "done":
                forced_t = forced_t or now_f
                if now_f - forced_t >= 1.5:    # leave the "Saved" message up for a moment
                    forced_next()
            elif wiz.state == "fail":
                if forced_t is None:
                    forced_t, forced_fails = now_f, forced_fails + 1
                elif now_f - forced_t >= 3.0:  # retry the failed step automatically
                    forced_t = None
                    if forced_stage == "calib":
                        cal = StickCal()
                    else:
                        bmap = ButtonMap()
        if {"start", "select"} <= inp.held:
            code = EXIT_POWER

        sid, stag, score, _ext, sname = vsys[sys_idx] if vsys else NO_SYSTEM
        if view == "recents":
            games, key = recent_games(), "\0recents"
        else:
            games, key = games_of(sid), sid
        idx = min(idx_by.get(key, 0), max(0, len(games) - 1))
        clk = time.strftime("%H:%M")

        canvas.fill(BG)
        pygame.draw.rect(canvas, PANEL, (0, 0, W, 64))
        pygame.draw.rect(canvas, ACCENT, (0, 62, W, 2))
        canvas.blit(f_logo.render("RG", True, ACCENT), (20, 14))
        canvas.blit(f_logo.render("OS", True, TEXT), (64, 14))
        if view == "games":
            info = "%s   %d %s%s   %s" % (sname, len(games), "port" if sid == "ports" else "game", "" if len(games) == 1 else "s", clk)
        elif view == "systems":
            info = "Games   %s" % clk
        elif view == "recents":
            info = "Recents   %d game%s   %s" % (len(games), "" if len(games) == 1 else "s", clk)
        elif view == "tools":
            info = "Tools   %s" % clk
        elif view in ("settings", "calib", "bmap"):
            info = "Settings   %s" % clk
        elif view == "info":
            info = "Settings > Information   %s" % clk
        elif view == "wifi" or (view == "kbd" and kb.field.startswith("wifi")):
            info = "Settings > Wi-Fi   %s" % clk
        else:
            info = clk
        if forced:
            info = "First-time setup   %s" % clk
        s = f_small.render(info, True, DIM)
        bat = read_battery()
        right = W - 20
        if bat:                                    # battery gauge at the top right (when /run/rgos/battery exists)
            pct, status = bat
            low = pct <= 15 and status.lower() != "charging"
            col = (230, 70, 60) if low else ACCENT if status.lower() == "charging" else TEXT
            bx, by = W - 90, 22
            pygame.draw.rect(canvas, col, (bx, by, 30, 16), 2, border_radius=3)
            pygame.draw.rect(canvas, col, (bx + 30, by + 4, 3, 8))
            fw = max(2, 24 * pct // 100)
            pygame.draw.rect(canvas, col, (bx + 3, by + 3, fw, 10))
            pt = f_tiny.render("%d%%" % pct + ("+" if status.lower() == "charging" else ""), True, col)
            canvas.blit(pt, (bx + 38, by + 2))
            right = bx - 16
        canvas.blit(s, (right - s.get_width(), 24))

        if view == "home":
            gap = 20
            bw, bh = min(320, (W - 20 - gap) // 2), 160
            x0 = (W - (2 * bw + gap)) // 2
            subs = {"games": "%d port%s" % (len(all_games), "" if len(all_games) == 1 else "s"),
                    "recents": "%d recent" % len(recent_games()),
                    "tools": "RetroArch",
                    "settings": "Theme, Wi-Fi, info, sticks, buttons"}
            for i, (hid, hname) in enumerate(HOME_ITEMS):
                bx, by, sel = x0 + (i % 2) * (bw + gap), top_y + (i // 2) * (bh + gap), i == home_idx
                pygame.draw.rect(canvas, SEL_BG if sel else PANEL, (bx, by, bw, bh), border_radius=22)
                if sel:
                    pygame.draw.rect(canvas, ACCENT, (bx, by, bw, bh), 3, border_radius=22)
                ICONS[hid](canvas, bx + 70, by + bh // 2, ACCENT if sel else DIM, SEL_BG if sel else PANEL)
                canvas.blit(f_logo.render(hname, True, TEXT if sel else DIM), (bx + 124, by + bh // 2 - 28))
                canvas.blit(f_small.render(fit(f_small, subs[hid], bw - 126 - 10), True, DIM), (bx + 126, by + bh // 2 + 12))
            hint = "A Open   X Rescan   START+SELECT Power off"
        elif view == "tools":
            for i, (_tid, tname, tdesc, _tgt) in enumerate(TOOLS):
                by, sel = top_y + i * 56, i == tool_idx
                pygame.draw.rect(canvas, SEL_BG if sel else PANEL, (list_x, by, W - 2 * list_x, 52), border_radius=12)
                if sel:
                    pygame.draw.rect(canvas, ACCENT, (list_x, by, W - 2 * list_x, 52), 2, border_radius=12)
                canvas.blit(f_row.render(tname, True, TEXT if sel else DIM), (list_x + 24, by + 5))
                canvas.blit(f_small.render(fit(f_small, tdesc, W - 2 * list_x - 48), True, DIM), (list_x + 24, by + 30))
            hint = "A Open   B Back   START+SELECT Power off"
        elif view == "settings":
            wst = wifictl.status()
            spl = {"on": "On: video and sound", "silent": "On: video only", "off": "Off"}[splash.mode_of(state)]
            srows = {"theme": ("Theme", "%s   (%d of %d)   Left/Right changes it" % (THEMES[theme_index(theme_id)][1], theme_index(theme_id) + 1, len(THEMES))),
                     "splash": ("Startup splash", spl + "   (plays once at power on)   X previews"),
                     "calib": ("Calibrate sticks", "Fixes swapped or mixed-up stick directions in menu, games and ports"),
                     "bmap": ("Map buttons", "Teach RG OS which physical button is A, B, D-pad, ... (if buttons are wrong)"),
                     "wifi": ("Wi-Fi", wifictl.status_text(wst)),
                     "info": ("Information", "Build, kernel, storage, battery, volume and input status")}
            for i, (nm, sub) in enumerate(srows[r] for r in SETTINGS_ROWS):
                by, sel = top_y + i * 46, i == set_idx
                pygame.draw.rect(canvas, SEL_BG if sel else PANEL, (list_x, by, W - 2 * list_x, 42), border_radius=12)
                if sel:
                    pygame.draw.rect(canvas, ACCENT, (list_x, by, W - 2 * list_x, 42), 2, border_radius=12)
                canvas.blit(f_row.render(nm, True, TEXT if sel else DIM), (list_x + 24, by + 3))
                sub_w = W - 2 * list_x - 48
                if SETTINGS_ROWS[i] == "theme":      # colour swatches of the active theme at the right end of the row
                    sw = [THEMES[theme_index(theme_id)][2][k] for k in ("BG", "PANEL", "SEL_BG", "ACCENT", "TEXT")]
                    sx = W - list_x - 24 - len(sw) * 24
                    for j, col in enumerate(sw):
                        pygame.draw.rect(canvas, col, (sx + j * 24, by + 11, 20, 20), border_radius=5)
                        pygame.draw.rect(canvas, DIM, (sx + j * 24, by + 11, 20, 20), 1, border_radius=5)
                    sub_w = sx - (list_x + 24) - 12
                canvas.blit(f_small.render(fit(f_small, sub, sub_w), True, DIM), (list_x + 24, by + 24))
            hint = ("Left/Right Change theme   " if SETTINGS_ROWS[set_idx] == "theme" else
                    "A Change   X Preview   " if SETTINGS_ROWS[set_idx] == "splash" else "A Open   ") + "B Back   START+SELECT Power off"
        elif view == "info":
            pygame.draw.rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 336), border_radius=10)
            info_rows = settings_info()
            for i, (k, v) in enumerate(info_rows):
                y = top_y + 10 + i * (34 if len(info_rows) <= 9 else 31)      # 10 rows (with SD card 2) still fit the panel
                canvas.blit(f_row.render(k, True, DIM), (list_x + 24, y))
                canvas.blit(f_row.render(fit(f_row, v, W - 2 * list_x - 48 - 190), True, TEXT), (list_x + 214, y))
            hint = "B Back   START+SELECT Power off"
        elif view == "wifi":
            wst, wrows = wifictl.status(), wscan.rows(wconf)
            wn = len(wrows) + 2
            pygame.draw.rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 336), border_radius=10)
            head = wifictl.status_text(wst) if wconf["enabled"] else "Wi-Fi is off"
            canvas.blit(f_row.render(fit(f_row, head, W - 2 * list_x - 56), True, ACCENT), (list_x + 28, top_y + 8))
            if wst["state"] == "connected" and wst["rssi"] is not None:
                canvas.blit(f_small.render("%d dBm" % wst["rssi"], True, DIM), (W - list_x - 90, top_y + 12))
            vis = 7
            first = min(max(0, wf_idx - vis // 2), max(0, wn - vis))
            for slot in range(min(vis, wn)):
                i = first + slot
                y, sel = top_y + 44 + slot * 40, i == wf_idx
                if sel:
                    pygame.draw.rect(canvas, SEL_BG, (list_x + 6, y, W - 2 * list_x - 12, 38), border_radius=8)
                    pygame.draw.rect(canvas, ACCENT, (list_x + 6, y, 5, 38), border_radius=3)
                col = TEXT if sel else DIM
                if i == 0:
                    on = wconf["enabled"]
                    pygame.draw.rect(canvas, ACCENT if on else DIM, (list_x + 28, y + 11, 16, 16), 0 if on else 2, border_radius=3)
                    canvas.blit(f_row.render("Wi-Fi", True, col), (list_x + 56, y + 8))
                    t = f_small.render("On" if on else "Off", True, DIM)
                    canvas.blit(t, (W - list_x - 28 - t.get_width(), y + 12))
                elif i == wn - 1:
                    canvas.blit(f_row.render("Join hidden network...", True, col), (list_x + 56, y + 8))
                else:
                    net = wrows[i - 1]
                    label = net["ssid"] + ("  (saved)" if net["saved"] else "") + ("  (not supported)" if net["unsupported"] else "")
                    canvas.blit(f_row.render(fit(f_row, label, W - 2 * list_x - 200), True, col), (list_x + 56, y + 8))
                    lock = f_tiny.render("WPA" if net["secured"] else "open", True, DIM)
                    canvas.blit(lock, (W - list_x - 100 - lock.get_width(), y + 14))
                    for b in range(4):
                        pygame.draw.rect(canvas, ACCENT if b < wifictl.bars(net["rssi"]) else PANEL if sel else BG,
                                         (W - list_x - 70 + b * 12, y + 26 - b * 5 - 4, 8, 6 + b * 5))
            if time.time() < wf_msg_until:
                note = wf_msg
            elif wscan.busy:
                note = "Scanning..."
            elif wscan.err:
                note = wscan.err
            elif not wconf["enabled"]:
                note = "Turn Wi-Fi on to scan"
            elif wscan.seen and not wrows:
                note = "No networks found - X to scan again"
            else:
                note = ""
            if note:
                canvas.blit(f_small.render(fit(f_small, note, W - 2 * list_x - 56), True, TEXT), (list_x + 28, top_y + 310))
            hint = "A Connect   X Scan   Y Forget   B Back"
        elif view == "kbd":
            KW = 636 + DX
            KX = (W - KW) // 2
            canvas.blit(f_small.render(kb.title, True, DIM), (KX, top_y - 4))
            pygame.draw.rect(canvas, PANEL, (KX, top_y + 16, KW, 40), border_radius=8)
            pygame.draw.rect(canvas, ACCENT, (KX, top_y + 16, KW, 40), 2, border_radius=8)
            shown = kb.text if kb.show else "*" * len(kb.text)
            canvas.blit(f_row.render(tail_fit(f_row, shown, 590 + DX) + ("_" if int(time.time() * 2) % 2 == 0 else ""), True, TEXT),
                        (KX + 14, top_y + 25))
            for r in range(5):
                keys = kb.keys(r)
                ky = top_y + 72 + r * 50 + (6 if r == 4 else 0)
                kw = (KW - 9 * 4) // 10 if r < 4 else (KW - 4 * 4) // 5
                for c, k in enumerate(keys):
                    kx = KX + c * (kw + 4)
                    sel = (r == kb.row and c == kb.col)
                    pygame.draw.rect(canvas, SEL_BG if sel else PANEL, (kx, ky, kw, 44), border_radius=8)
                    if sel:
                        pygame.draw.rect(canvas, ACCENT, (kx, ky, kw, 44), 2, border_radius=8)
                    label = KB_LAYER_NAMES[(kb.layer + 1) % 3] if k == "Layer" else k
                    t = f_row.render(label, True, ACCENT if (sel and r == 4) else (TEXT if sel else DIM))
                    canvas.blit(t, (kx + (kw - t.get_width()) // 2, ky + 11))
            hint = "A Type   B Delete   START Done" + ("   X Show/hide" if kb.secret else "")
        elif view == "calib":
            pygame.draw.rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 330), border_radius=14)
            canvas.blit(f_big.render("Stick calibration", True, ACCENT), (list_x + 28, top_y + 22))
            if cal.state in ("base", "prompt", "release", "apply"):
                n = min(cal.step + 1, len(cal.PROMPTS))
                canvas.blit(f_small.render("Step %d of %d" % (n, len(cal.PROMPTS)), True, DIM), (list_x + 28, top_y + 58))
            for i, line in enumerate(cal.lines()):
                for j, wl in enumerate(wrap(f_big if i == 0 else f_row, line, W - 2 * list_x - 60)):
                    canvas.blit((f_big if i == 0 else f_row).render(wl, True, TEXT if i == 0 else DIM),
                                (list_x + 28, top_y + 110 + i * 56 + j * 26))
            if cal.state == "fail":
                hint = "A/B Back"
            elif cal.state == "done":
                hint = "A/B Back"
            else:
                hint = "B Cancel"
            if forced:
                hint = "Step 1 of 2 - required first-time setup" + ("   MENU Skip" if forced_fails >= 3 else "")
        elif view == "bmap":
            pygame.draw.rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 330), border_radius=14)
            canvas.blit(f_big.render("Button mapping", True, ACCENT), (list_x + 28, top_y + 22))
            if bmap.state in ("prompt", "apply"):
                n = min(bmap.step + 1, len(bmap.PROMPTS))
                canvas.blit(f_small.render("Button %d of %d" % (n, len(bmap.PROMPTS)), True, DIM), (list_x + 28, top_y + 58))
            for i, line in enumerate(bmap.lines()):
                for j, wl in enumerate(wrap(f_big if i == 0 else f_row, line, W - 2 * list_x - 60)):
                    canvas.blit((f_big if i == 0 else f_row).render(wl, True, TEXT if i == 0 else DIM),
                                (list_x + 28, top_y + 110 + i * 56 + j * 26))
            hint = "A/B Back" if bmap.state in ("done", "fail") else "Press the button shown (wizard stops by itself if idle)"
            if forced:
                hint = "Step %d of %d - required first-time setup" % (2 if HAS_STICKS else 1, 2 if HAS_STICKS else 1) + ("   MENU Skip" if forced_fails >= 3 else "")
        elif view == "systems":
            if not vsys:
                for i, m in enumerate(["No games found.",
                                       "Put ROMs in  roms/gba/  or launcher scripts (Name.sh)",
                                       "in  roms/ports/  on the ROMS partition, then press X (B = back)."]):
                    canvas.blit((f_big if i == 0 else f_row).render(m, True, TEXT if i == 0 else DIM),
                                (40, 150 + i * 34))
            sys_rows = 6                       # rows that fit between the header and the hint line
            sys_first = max(0, min(sys_idx - sys_rows // 2, len(vsys) - sys_rows))
            for r, (xid, xtag, xcore, _e, xname) in enumerate(vsys):
                if not sys_first <= r < sys_first + sys_rows:
                    continue
                y, sel, cnt = top_y + (r - sys_first) * 56, r == sys_idx, len(games_of(xid))
                if sel:
                    pygame.draw.rect(canvas, SEL_BG, (list_x, y, list_w, 52), border_radius=8)
                    pygame.draw.rect(canvas, ACCENT, (list_x, y, 5, 52), border_radius=3)
                pygame.draw.rect(canvas, PANEL if not sel else BG, (list_x + 18, y + 6, 64, 40), border_radius=6)
                t = f_big.render(xtag, True, ACCENT if sel else DIM)
                canvas.blit(t, (list_x + 50 - t.get_width() // 2, y + 13))
                canvas.blit(f_row.render(fit(f_row, xname, list_w - 220), True, TEXT if sel else DIM),
                            (list_x + 98, y + 4))
                canvas.blit(f_small.render("%d %s%s" % (cnt, "port" if xid == "ports" else "game",
                                                         "" if cnt == 1 else "s"), True, DIM),
                            (list_x + 98, y + 28))
            px, cnt = 500 + DX, len(games)
            if vsys:
                pygame.draw.rect(canvas, PANEL, (px, top_y, 200, 352), border_radius=10)
                pygame.draw.rect(canvas, SEL_BG, (px + 20, top_y + 20, 160, 110), border_radius=8)
                ini = f_logo.render(stag, True, ACCENT)
                canvas.blit(ini, (px + 100 - ini.get_width() // 2, top_y + 52))
                for i, line in enumerate(wrap(f_row, sname, 170)):
                    canvas.blit(f_row.render(line, True, TEXT), (px + 15, top_y + 148 + i * 26))
                canvas.blit(f_small.render("%d %s%s" % (cnt, "port" if sid == "ports" else "game", "" if cnt == 1 else "s"), True, DIM),
                            (px + 15, top_y + 300))
                canvas.blit(f_small.render("Native Linux games" if score == "native" else "RetroArch + mGBA", True, DIM), (px + 15, top_y + 322))
            if not os.path.isdir(ROMS_DIR):
                canvas.blit(f_small.render("ROMS partition not found.", True, ACCENT), (list_x + 18, H - 52))
            hint = "A Open   B Back   X Rescan   START+SELECT Power off"
        elif not games:
            if view == "recents":
                msg = ["No recent games yet.",
                       "Games you run show up here, newest first.",
                       "(B = back)"]
            else:
                msg = ["No games found.",
                       "Put ROMs in  roms/gba/  or launcher scripts (Name.sh)",
                       "in  roms/ports/  on the ROMS partition, then X (B = back)."]
            for i, m in enumerate(msg):
                canvas.blit((f_big if i == 0 else f_row).render(m, True, TEXT if i == 0 else DIM),
                            (40, 150 + i * 34))
            hint = "B Back   X Rescan   START+SELECT Power off"
        else:
            first = max(0, min(idx - rows // 2, len(games) - rows))
            for r in range(rows):
                gi = first + r
                if gi >= len(games):
                    break
                y = top_y + r * row_h
                if gi == idx:
                    pygame.draw.rect(canvas, SEL_BG, (list_x, y, list_w, row_h - 4), border_radius=8)
                    pygame.draw.rect(canvas, ACCENT, (list_x, y, 5, row_h - 4), border_radius=3)
                badge = f_tiny.render("SD2", True, ACCENT) if games[gi].get("card", 1) == 2 else None
                canvas.blit(f_row.render(fit(f_row, games[gi]["title"], list_w - 40 - (36 if badge else 0)),
                                         True, TEXT if gi == idx else DIM), (list_x + 18, y + 9))
                if badge:
                    canvas.blit(badge, (list_x + list_w - 34, y + 14))
            if len(games) > rows:
                bar_h = H - 64 - top_y
                th = max(24, int(bar_h * rows / len(games)))
                ty = top_y + int((bar_h - th) * first / max(1, len(games) - rows))
                pygame.draw.rect(canvas, PANEL, (list_x + list_w + 6, top_y, 6, bar_h), border_radius=3)
                pygame.draw.rect(canvas, ACCENT, (list_x + list_w + 6, ty, 6, th), border_radius=3)

            g = games[idx]
            px = 500 + DX
            pygame.draw.rect(canvas, PANEL, (px, top_y, 200, 352), border_radius=10)
            pygame.draw.rect(canvas, SEL_BG, (px + 20, top_y + 14, 160, 130), border_radius=8)
            ini = f_logo.render(g["title"][:1].upper(), True, ACCENT)
            canvas.blit(ini, (px + 100 - ini.get_width() // 2, top_y + 62))
            ty_ = top_y + 152
            for line in wrap_n(f_row, g["title"], 170, 2):
                canvas.blit(f_row.render(line, True, TEXT), (px + 15, ty_))
                ty_ += 24
            canvas.blit(f_small.render("%.1f MB" % (g["size"] / 1048576), True, DIM),
                        (px + 15, top_y + 300))
            canvas.blit(f_small.render("Native port", True, DIM), (px + 15, top_y + 322))
            hint = "A Run   B Back   L/R Page   X Rescan   START+SELECT Power off"

        if inp.conf.get("debug") and inp.last_raw:
            hint += "   [" + inp.last_raw + "]"
        canvas.blit(f_small.render(hint, True, DIM), (20, H - 28))

        vol = read_volume()                     # volume popup (VOL+/VOL- are handled by the rgos-volume service)
        if vol and vol[1] != vol_seen:
            vol_seen, vol_pct, vol_show_until = vol[1], vol[0], time.time() + 1.5
        if time.time() < vol_show_until:
            bx, by, bw, bh = W // 2 - 130, H - 100, 260, 44
            pygame.draw.rect(canvas, PANEL, (bx, by, bw, bh), border_radius=10)
            pygame.draw.rect(canvas, ACCENT, (bx, by, bw, bh), 2, border_radius=10)
            canvas.blit(f_small.render("VOL", True, TEXT), (bx + 14, by + 14))
            pygame.draw.rect(canvas, BG, (bx + 56, by + 17, 140, 10), border_radius=5)
            if vol_pct:
                pygame.draw.rect(canvas, ACCENT, (bx + 56, by + 17, max(6, 140 * vol_pct // 100), 10), border_radius=5)
            canvas.blit(f_small.render("%d%%" % vol_pct, True, TEXT), (bx + 208, by + 14))

        bri = read_brightness()                 # brightness popup (SELECT+VOL, handled by the rgos-hw service)
        if bri and bri[1] != bri_seen:
            if bri_seen is not None:
                bri_pct, bri_show_until = bri[0], time.time() + 1.5
            bri_seen = bri[1]
        if time.time() < bri_show_until:
            bx, by, bw, bh = W // 2 - 130, H - 152, 260, 44
            pygame.draw.rect(canvas, PANEL, (bx, by, bw, bh), border_radius=10)
            pygame.draw.rect(canvas, ACCENT, (bx, by, bw, bh), 2, border_radius=10)
            canvas.blit(f_small.render("LIGHT", True, TEXT), (bx + 12, by + 14))
            pygame.draw.rect(canvas, BG, (bx + 66, by + 17, 130, 10), border_radius=5)
            pygame.draw.rect(canvas, ACCENT, (bx + 66, by + 17, max(6, 130 * bri_pct // 100), 10), border_radius=5)
            canvas.blit(f_small.render("%d%%" % bri_pct, True, TEXT), (bx + 208, by + 14))

        present()
        clock.tick(30)

    if not windowed:
        fbout.set_graphics(False)
    pygame.quit()
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        log("CRASH\n" + traceback.format_exc())
        fbout.set_graphics(False)      # give the text console back so the error is readable
        sys.exit(1)
