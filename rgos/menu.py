#!/usr/bin/env python3
"""RG OS menu - original frontend for the Anbernic RG Rotate, RG34XXSP and RG35XX H.

Lists the native Linux ports in <ROMS>/roms/ports/ (the .sh launchers) and on selection writes the script path to
/run/rgos/launch and exits with code 10 so the session loop can run it.
Exit code 20 = power off.
"""
import glob
import io, json, math, os, random, re, signal, subprocess, sys, threading, time
import pygame
from evpad import EvPads, CODES as EV_CODES, EV_KEY, EV_ABS
import fbout
import splash
import wifictl
import btctl
import sshctl
import filemanager
try:
    import sdcard                      # second SD card (optional: the menu works without it)
except Exception:
    sdcard = None

ROMS_ROOT = os.environ.get("RGOS_ROMS", "/media/roms")
# (folder name, short tag, runner, accepted extensions, full name)
# Ports: native Linux games started by a launcher script (roms/ports/<Name>.sh, data in a sub-folder); rgos-session runs the script itself.
SYSTEMS = [("gba", "GBA", "retroarch", (".gba",), "Game Boy Advance"),
           ("gb", "GB", "retroarch", (".gb",), "Game Boy"),
           ("gbc", "GBC", "retroarch", (".gbc",), "Game Boy Color"),
           ("nes", "NES", "retroarch", (".nes", ".unf", ".unif"), "Nintendo Entertainment System"),
           ("snes", "SNES", "retroarch", (".sfc", ".smc", ".swc", ".fig", ".bs"), "Super Nintendo Entertainment System"),
           # Genesis Plus GX-supported Sega platforms. Each has its own ROM folder so
           # games are grouped correctly even though they all use the same libretro core.
           ("genesis", "MD", "retroarch", (".md", ".gen", ".smd", ".bin"), "Sega Mega Drive / Genesis"),
           ("mastersystem", "SMS", "retroarch", (".sms",), "Sega Master System / Mark III"),
           ("gamegear", "GG", "retroarch", (".gg",), "Sega Game Gear"),
           ("sg1000", "SG", "retroarch", (".sg", ".sc", ".sf"), "Sega SG-1000 / SC-3000"),
           ("segacd", "CD", "retroarch", (".cue", ".chd", ".iso", ".m3u", ".ccd"), "Sega CD / Mega-CD"),
           ("sega32x", "32X", "retroarch", (".32x", ".md", ".gen", ".smd", ".bin"), "Sega 32X"),
           ("segapico", "PICO", "retroarch", (".pico", ".bin"), "Sega Pico"),
           # Atari consoles: Stella (2600), Atari800 (5200, needs 5200.rom in ROMS/bios/atari5200/), ProSystem (7800).
           ("atari2600", "A26", "retroarch", (".a26", ".bin"), "Atari 2600"),
           ("atari5200", "A52", "retroarch", (".a52", ".bin", ".car"), "Atari 5200"),
           ("atari7800", "A78", "retroarch", (".a78", ".bin"), "Atari 7800"),
           # Neo Geo Pocket and Pocket Color (Beetle NeoPop; RACE is the lighter alternative). No BIOS needed.
           # Neo Geo Pocket: monochrome games in roms/ngp, Color games in roms/ngpc (same core; extensions accepted in both folders).
           ("ngp", "NGP", "retroarch", (".ngp", ".ngc", ".ngpc"), "Neo Geo Pocket"),
           ("ngpc", "NGPC", "retroarch", (".ngc", ".ngpc", ".ngp"), "Neo Geo Pocket Color"),
           # WonderSwan and WonderSwan Color (Beetle Cygne). No BIOS needed.
           # roms/ws for the original WonderSwan, roms/wsc for WonderSwan Color (same core; extensions accepted in both folders).
           ("ws", "WS", "retroarch", (".ws", ".wsc", ".pc2"), "WonderSwan"),
           ("wsc", "WSC", "retroarch", (".wsc", ".ws", ".pc2"), "WonderSwan Color"),
           # PlayStation: pcsx_rearmed (built from source, fast on ARM) or Beetle PSX from Debian. Disc images only (.cue/.chd/.pbp/.m3u/.iso);
           # .bin is not listed because a .cue already points at it. A BIOS (scph1001.bin, scph5501.bin, ...) in ROMS/bios/ is recommended.
           ("psx", "PSX", "retroarch", (".cue", ".chd", ".pbp", ".m3u", ".iso", ".img", ".ccd"), "Sony PlayStation"),
           # Nintendo GameCube (Dolphin libretro core), RG Rotate only; no Wii. Disc images/dumps only. .iso is shared with Wii dumps,
           # so keep Wii games out of roms/gc (.wbfs/.wad are not listed). Needs the GameCube BIOS (IPL.bin) in ROMS/bios/dolphin-emu/Sys/GC/<region>/ for the boot logo (optional).
           ("gc", "GC", "retroarch", (".gcm", ".iso", ".gcz", ".rvz", ".ciso", ".m3u", ".dol", ".elf"), "Nintendo GameCube"),
           # FBNeo runs arcade boards (CPS1/2/3, Konami, Irem, ...) and Neo Geo; ROMs stay zipped, named as MAME/FBNeo expect.
           ("arcade", "ARC", "retroarch", (".zip", ".7z"), "Arcade (FinalBurn Neo)"),
           ("neogeo", "NEO", "retroarch", (".zip", ".7z"), "Neo Geo"),
           ("ports", "PORT", "native", (".sh",), "Ports")]
ROMS_DIR = os.path.join(ROMS_ROOT, "roms")
RUN_DIR = os.environ.get("RGOS_RUN", "/run/rgos")
STATE_FILE = os.environ.get("RGOS_STATE", "/var/lib/rgos/state.json")
FAVORITES_FILE = os.environ.get("RGOS_FAVORITES", os.path.join(ROMS_ROOT, "favorites.txt"))
SPLASH_DONE = os.path.join(RUN_DIR, "splash-done")      # exists once the startup splash has played (/run is cleared at every boot)
INPUT_CONF = os.environ.get("RGOS_INPUT", "/etc/rgos/input.json")
EXIT_LAUNCH, EXIT_POWER = 10, 20
def _device():
    try:
        with open("/etc/rgos-device") as f:
            return f.read().strip()
    except OSError:
        return ""


# The logical canvas depends on the device and fbout scales it uniformly to the framebuffer (a panel with the same aspect is
# filled exactly, no bars, no stretching):
#   rg-rotate  square (1:1) screen, 720x720
#   rg34xxsp   3:2 screen, 720x480 (the layout every screen below was first written for)
#   rg35xxh    4:3 screen (640x480 panel), 720x540 canvas (DY = 60: one more row than the RG34XXSP)
# DY is the height gained over the 720x480 layout (0 on the RG34XXSP); every screen below spends it on more rows / taller
# panels instead of leaving blank space.
DEVICE_ID = os.environ.get("RGOS_DEVICE") or _device() or "rg-rotate"
CANVAS = {"rg-rotate": (720, 720), "rg34xxsp": (720, 480), "rg35xxh": (720, 540)}
W, H = CANVAS.get(DEVICE_ID, CANVAS["rg-rotate"])
DY = H - 480
DX = 0                                         # horizontal layout offset (unused: the width is unchanged)

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
    ("mustard", "MustardOS", dict(BG=(24, 22, 18), PANEL=(38, 35, 28), SEL_BG=(84, 70, 20), ACCENT=(240, 184, 0), TEXT=(245, 238, 220), DIM=(158, 148, 118))),
    ("contrast", "High Contrast", dict(BG=(0, 0, 0), PANEL=(28, 28, 28), SEL_BG=(72, 72, 72), ACCENT=(255, 235, 0), TEXT=(255, 255, 255), DIM=(176, 176, 176))),
]
DEFAULT_THEME = THEMES[0][0]
BG = PANEL = SEL_BG = ACCENT = TEXT = DIM = (0, 0, 0)
# XMB launcher colours per theme: (gradient top-left, gradient bottom-right, silk tint). The XMB colour comes only from the chosen
# theme (never from the date or time of day, as on a real PS3), so changing the theme recolours the whole launcher.
XMB_COLORS = {"midnight": ((9, 11, 28), (62, 52, 130), (190, 180, 255)),
              "ocean": ((4, 18, 34), (14, 110, 160), (170, 235, 255)),
              "forest": ((8, 24, 12), (70, 130, 50), (205, 245, 190)),
              "sunset": ((30, 10, 24), (190, 80, 60), (255, 215, 170)),
              "purple": ((18, 10, 32), (120, 60, 190), (235, 205, 255)),
              "amber": ((0, 0, 0), (140, 90, 0), (255, 220, 130)),
              "gameboy": ((12, 46, 12), (110, 150, 30), (220, 240, 150)),
              "paper": ((226, 223, 213), (168, 184, 214), (255, 255, 255)),
              "mustard": ((22, 20, 14), (170, 130, 20), (255, 235, 170)),
              "contrast": ((0, 0, 0), (50, 50, 50), (255, 255, 255))}
XMB_TOP = XMB_LOW = XMB_SILK = (0, 0, 0)


def theme_index(tid):
    for i, t in enumerate(THEMES):
        if t[0] == tid:
            return i
    return 0


def apply_theme(tid):
    """Make the theme with this id the active colour set (unknown ids fall back to the default) and return the id actually used."""
    global BG, PANEL, SEL_BG, ACCENT, TEXT, DIM, XMB_TOP, XMB_LOW, XMB_SILK
    tid, _name, c = THEMES[theme_index(tid)]
    BG, PANEL, SEL_BG, ACCENT, TEXT, DIM = c["BG"], c["PANEL"], c["SEL_BG"], c["ACCENT"], c["TEXT"], c["DIM"]
    XMB_TOP, XMB_LOW, XMB_SILK = XMB_COLORS.get(tid, (BG, ACCENT, TEXT))
    return tid


apply_theme(DEFAULT_THEME)

DEFAULT_INPUT = {"debug": False}
KEYMAP = {pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left",
          pygame.K_RIGHT: "right", pygame.K_RETURN: "a", pygame.K_z: "a",
          pygame.K_ESCAPE: "b", pygame.K_x: "b", pygame.K_q: "l", pygame.K_w: "r",
          pygame.K_PAGEUP: "l", pygame.K_PAGEDOWN: "r", pygame.K_TAB: "select",
          pygame.K_SPACE: "start", pygame.K_c: "x", pygame.K_y: "y"}
REPEATABLE = {"up", "down", "left", "right", "l", "r"}
STICK_AXES = (0x00, 0x01)                      # ABS_X / ABS_Y: the left stick, as published by the input hub (-32767..32767)
STICK_ON, STICK_OFF = 0.55, 0.35               # fraction of full travel: push past ON to start a direction, drop below OFF to end it
STICK_MAX = 32767.0


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
        self.held, self.repeat, self.last_raw = set(), {}, ""
        self.ev, self.ev_seen = EvPads(), False
        self.stick, self.stick_dir = {0x00: 0.0, 0x01: 0.0}, None      # left stick position (-1..1 per axis) and the direction it holds

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

    def _stick_update(self, out):
        """Left stick as a D-pad: the axis pushed furthest decides the direction (no diagonals), with hysteresis so a stick that
        hovers around the threshold does not flicker. It uses the same hold / auto-repeat as the D-pad buttons."""
        x, y = self.stick[0x00], self.stick[0x01]
        ax, ay = abs(x), abs(y)
        cur = self.stick_dir
        if cur is not None:                        # holding a direction: keep it until its own axis falls back below OFF
            mine, other = (ax, ay) if cur in ("left", "right") else (ay, ax)
            if mine >= STICK_OFF and mine >= other:
                return
        if max(ax, ay) >= STICK_ON:
            new = ("right" if x > 0 else "left") if ax >= ay else ("down" if y > 0 else "up")
        else:
            new = None
        if new == cur:
            return
        if cur is not None:
            self._up(cur)
        self.stick_dir = new
        if new is not None:
            self._down(new, out)

    def poll(self):
        out = []
        evs = self.ev.read()
        if os.path.exists(SCREENOFF_FILE):         # screen asleep (rgos-hw): buttons pressed in the dark do nothing
            self.held.clear()
            self.repeat.clear()
            self.stick, self.stick_dir = {0x00: 0.0, 0x01: 0.0}, None
            evs = []
            for e in pygame.event.get():           # keyboard events must not act in the dark either
                if e.type == pygame.QUIT:
                    out.append("quit")
            return out
        for t, c, v in evs:
            if t == EV_KEY and c in EV_CODES:
                self.ev_seen = True
                self.last_raw = "ev 0x%x=%s" % (c, EV_CODES[c])
                if v == 1:
                    self._down(EV_CODES[c], out)
                elif v == 0:
                    self._up(EV_CODES[c])
            elif t == EV_ABS and c == 0x10:                # D-pad reported as a hat
                self._dir("left", "right", v, out)
            elif t == EV_ABS and c == 0x11:
                self._dir("up", "down", v, out)
            elif t == EV_ABS and c in STICK_AXES:          # left stick: navigates like the D-pad
                self.stick[c] = max(-1.0, min(1.0, v / STICK_MAX))
                self._stick_update(out)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                out.append("quit")
            elif e.type == pygame.KEYDOWN and e.key in KEYMAP:
                self._down(KEYMAP[e.key], out)
            elif e.type == pygame.KEYUP and e.key in KEYMAP:
                self._up(KEYMAP[e.key])
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


def load_favorites():
    """Read favorite ROM paths from the persistent, newline-delimited favorites.txt."""
    try:
        with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
            return list(dict.fromkeys(line.strip() for line in f if line.strip()))
    except OSError:
        return []


def save_favorites(paths):
    """Persist favorite paths without losing the existing list if writing fails."""
    try:
        os.makedirs(os.path.dirname(FAVORITES_FILE) or ".", exist_ok=True)
        tmp = FAVORITES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(path + "\n" for path in sorted(set(paths)))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, FAVORITES_FILE)
        return True
    except OSError as e:
        log("favorites save failed: %s" % e)
        try:
            os.unlink(FAVORITES_FILE + ".tmp")
        except OSError:
            pass
        return False


def installed_cores():
    """Return RetroArch core names actually installed on this device."""
    dirs = ("/usr/lib/aarch64-linux-gnu/libretro", "/usr/lib/libretro",
            "/usr/lib/aarch64-linux-gnu/retroarch/cores", "/usr/local/lib/libretro")
    found = set()
    for directory in dirs:
        try:
            for filename in os.listdir(directory):
                if filename.endswith("_libretro.so"):
                    found.add(filename[:-len("_libretro.so")])
        except OSError:
            pass
    return sorted(found)


def default_core(sid):
    return {"gba": "mgba", "gb": "gambatte", "gbc": "gambatte", "nes": "nestopia", "snes": "snes9x", "genesis": "genesis_plus_gx",
            "mastersystem": "genesis_plus_gx", "gamegear": "genesis_plus_gx",
            "sg1000": "genesis_plus_gx", "segacd": "genesis_plus_gx",
            "sega32x": "genesis_plus_gx", "segapico": "genesis_plus_gx",
            "arcade": "fbneo", "neogeo": "fbneo",
            "atari2600": "stella2014", "atari5200": "atari800", "atari7800": "prosystem",
            "ngp": "mednafen_ngp", "ngpc": "mednafen_ngp", "psx": "pcsx_rearmed", "gc": "dolphin", "ws": "mednafen_wswan", "wsc": "mednafen_wswan"}.get(sid, "")


# Only offer cores known to support the selected system. The list is intersected
# with installed_cores(), so unavailable cores never appear in the UI.
SYSTEM_CORE_COMPATIBILITY = {
    "gba": ("mgba", "gpsp", "vbam", "mednafen_gba"),
    "gb": ("gambatte", "sameboy", "gearboy", "mgba", "vbam", "mednafen_gb"),
    "gbc": ("gambatte", "sameboy", "gearboy", "mgba", "vbam", "mednafen_gb"),
    "nes": ("nestopia", "fceumm", "quicknes", "mesen", "mesen_s"),
    "snes": ("snes9x", "snes9x2010", "bsnes", "bsnes_hd_beta", "bsnes2014_accuracy", "bsnes2014_balanced", "bsnes2014_performance", "bsnes_mercury_accuracy", "bsnes_mercury_balanced", "bsnes_mercury_performance"),
    "genesis": ("genesis_plus_gx", "picodrive"),
    "mastersystem": ("genesis_plus_gx", "picodrive"),
    "gamegear": ("genesis_plus_gx", "picodrive"),
    "sg1000": ("genesis_plus_gx",),
    "segacd": ("genesis_plus_gx", "picodrive"),
    "sega32x": ("genesis_plus_gx", "picodrive"),
    "segapico": ("genesis_plus_gx",),
    "atari2600": ("stella2014", "stella"),
    "atari5200": ("atari800",),
    "atari7800": ("prosystem",),
    "ngp": ("mednafen_ngp", "race"),
    "ngpc": ("mednafen_ngp", "race"),
    "psx": ("pcsx_rearmed", "mednafen_psx", "mednafen_psx_hw", "swanstation"),
    "gc": ("dolphin",),
    "ws": ("mednafen_wswan",),
    "wsc": ("mednafen_wswan",),
    "arcade": ("fbneo",),
    "neogeo": ("fbneo",),
}


def compatible_cores(sid, cores=None):
    """Return installed cores known to support this system, in preferred order."""
    installed = set(installed_cores() if cores is None else cores)
    return [core for core in SYSTEM_CORE_COMPATIBILITY.get(sid, ()) if core in installed]


CORE_NAMES = {"mgba": "mGBA", "gpsp": "gpSP", "vbam": "VBA-M", "gambatte": "Gambatte", "sameboy": "SameBoy", "gearboy": "Gearboy",
              "nestopia": "Nestopia", "fceumm": "FCEUmm", "quicknes": "QuickNES", "mesen": "Mesen", "mesen_s": "Mesen-S",
              "snes9x": "Snes9x", "snes9x2010": "Snes9x 2010", "bsnes": "bsnes", "bsnes_hd_beta": "bsnes-hd",
              "genesis_plus_gx": "Genesis Plus GX", "picodrive": "PicoDrive", "stella2014": "Stella 2014", "stella": "Stella",
              "atari800": "Atari800", "prosystem": "ProSystem", "mednafen_ngp": "Beetle NeoPop", "race": "RACE",
              "mednafen_wswan": "Beetle Cygne", "pcsx_rearmed": "PCSX ReARMed", "mednafen_psx": "Beetle PSX",
              "mednafen_psx_hw": "Beetle PSX HW", "swanstation": "SwanStation", "dolphin": "Dolphin", "fbneo": "FinalBurn Neo",
              "mednafen_gba": "Beetle GBA", "mednafen_gb": "Beetle GB"}


def core_label(sid, state):
    """Display name of the RetroArch core that will run this system (the Settings > Emulators choice, else the default)."""
    core = state.get("emulators", {}).get(sid)
    if core not in SYSTEM_CORE_COMPATIBILITY.get(sid, ()):
        core = default_core(sid)
    return CORE_NAMES.get(core) or core.replace("_", " ") or "RetroArch"


# Only systems that can actually run are kept: native ports always, RetroArch systems only when at least one compatible core is
# installed. Systems without a core never show up in the menu and get no ROM folder. RGOS_ALL_SYSTEMS=1 keeps the full list (PC testing).
if os.environ.get("RGOS_ALL_SYSTEMS") != "1":
    _have_cores = installed_cores()
    SYSTEMS = [sy for sy in SYSTEMS if sy[2] != "retroarch" or compatible_cores(sy[0], _have_cores)]
    # GameCube is an RG Rotate feature only: hidden on every other device even if a dolphin core file ends up there.
    if (os.environ.get("RGOS_DEVICE") or _device() or "rg-rotate") != "rg-rotate":
        SYSTEMS = [sy for sy in SYSTEMS if sy[0] != "gc"]


def ensure_folders():
    """Create every folder RG OS uses on the ROMS partition (safe to run on every boot; the first boot creates them all, and a
    folder you delete comes back). Per-system folders are made for the supported systems only (see SYSTEMS above)."""
    shared = ("bios", "config", "firmware", "music", "saves", "screenshots", "splash", "states", "update")
    paths = [os.path.join(ROMS_ROOT, d) for d in shared] + [ROMS_DIR]
    for sy in SYSTEMS:
        sid = sy[0]
        paths += [os.path.join(ROMS_DIR, sid)]
        if sy[2] == "retroarch":                      # ports are native scripts: no saves/states of their own
            paths += [os.path.join(ROMS_ROOT, "saves", sid), os.path.join(ROMS_ROOT, "states", sid)]
    for d in paths:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            log("could not create %s: %s" % (d, e))


_FIT = {}                                      # (font, text, width) -> the fitted text (fit() is pure and runs for every label every frame)


def fit(font, text, width):
    key = (font, text, width)
    r = _FIT.get(key)
    if r is None:
        if len(_FIT) > 3000:
            _FIT.clear()
        r = text
        if font.size(r)[0] > width:
            while r and font.size(r + "...")[0] > width:
                r = r[:-1]
            r = r + "..."
        _FIT[key] = r
    return r


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


def _mix(c, t, k):
    """Blend colour c toward colour t by k (0..1)."""
    return tuple(max(0, min(255, int(c[i] + (t[i] - c[i]) * k))) for i in range(3))


class CFont:
    """pygame Font wrapper that remembers rendered text and measured widths. The menu redraws every frame, and re-rasterising the
    same strings 30 times a second was the biggest CPU cost on the handheld; now each string is rendered once per colour."""
    def __init__(self, font):
        self._f, self._r, self._s = font, {}, {}

    def render(self, text, aa, color, *bg):
        try:
            key = (text, aa, tuple(color), bg)
            surf = self._r.get(key)
        except TypeError:                      # unhashable argument: just render it
            return self._f.render(text, aa, color, *bg)
        if surf is None:
            if len(self._r) > 600:
                self._r.clear()
            surf = self._r[key] = self._f.render(text, aa, color, *bg)
        return surf

    def size(self, text):
        sz = self._s.get(text)
        if sz is None:
            if len(self._s) > 2000:
                self._s.clear()
            sz = self._s[text] = self._f.size(text)
        return sz

    def __getattr__(self, name):
        return getattr(self._f, name)


def throttled(fn, interval):
    """fn() at most once per `interval` seconds; in between the last result is returned (for sysfs / run-file polling)."""
    st = [-1e9, None]

    def get():
        now = time.monotonic()
        if now - st[0] >= interval:
            st[0], st[1] = now, fn()
        return st[1]
    return get


_BTN_CACHE = {}


def _btn_face(w, h, base, radius):
    """Cached face of a 3D button: vertical light-to-base gradient, rounded corners, thin top highlight."""
    key = (w, h, base, radius)
    surf = _BTN_CACHE.get(key)
    if surf is None:
        if len(_BTN_CACHE) > 600:
            _BTN_CACHE.clear()
        top, bot = _mix(base, (255, 255, 255), 0.16), _mix(base, (0, 0, 0), 0.12)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        for yy in range(h):
            pygame.draw.line(surf, _mix(top, bot, yy / max(1, h - 1)), (0, yy), (w, yy))
        hl = _mix(base, (255, 255, 255), 0.35)
        pygame.draw.line(surf, hl, (radius, 1), (w - radius - 1, 1))
        mask = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h), border_radius=radius)
        surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        _BTN_CACHE[key] = surf
    return surf


_BTN_ANIM = {}          # (x, y, w, h) -> [highlight 0..1, selected colour, unselected colour]; drives the select animation
ANIM_STEPS, ANIM_SPEED, LIFT_PX = 24, 16.0, 4   # colour steps (gradient cache), ease speed per second, lift of the home tiles
ANIM_DT = None                                  # fixed frame time override (used when recording video); None = real time
_anim_clock = [time.monotonic(), 1 / 30, False]  # last frame stamp, this frame's dt, "something is still moving"


def anim_tick():
    """Call once per frame before drawing: measures the frame time that every button animation advances by,
    so the motion takes the same real time at any frame rate (no stutter when the frame rate dips)."""
    now = time.monotonic()
    dt = ANIM_DT if ANIM_DT else min(0.05, max(0.001, now - _anim_clock[0]))
    _anim_clock[0], _anim_clock[1] = now, dt
    moving, _anim_clock[2] = _anim_clock[2], False
    return moving


XMB_UI = [False]                               # True while the XMB launcher is active: pages and buttons are drawn as translucent glass over its background
_GLASS = {}


def _glass_tint():
    """Glass colour for the active theme: dark glass on dark themes, white glass on light ones."""
    return (255, 255, 255) if sum(BG) / 3.0 > 140 else (0, 0, 0)


def _glass_face(w, h, radius, col, alpha, outline=45):
    key = (w, h, radius, col, alpha, outline, tuple(TEXT))
    g = _GLASS.get(key)
    if g is None:
        if len(_GLASS) > 160:
            _GLASS.clear()
        g = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
        pygame.draw.rect(g, tuple(col[:3]) + (alpha,), (0, 0, w, h), border_radius=radius)
        if outline:
            pygame.draw.rect(g, tuple(TEXT[:3]) + (outline,), (0, 0, w, h), 1, border_radius=radius)
        _GLASS[key] = g
    return g


def ui_rect(surf, col, rect, radius=0):
    """A filled rounded rect in the theme colour `col` (PANEL / SEL_BG / BG). On the XMB it is translucent glass so the silk shows through."""
    if not XMB_UI[0]:
        pygame.draw.rect(surf, col, rect, border_radius=radius)
        return
    x, y, w, h = rect
    if col == SEL_BG:
        surf.blit(_glass_face(w, h, radius, SEL_BG, 175), (x, y))
    else:
        surf.blit(_glass_face(w, h, radius, _glass_tint(), 105), (x, y))


def draw_button(surf, rect, base, radius=12, depth=4, sel=False):
    """Draw a button that looks raised: soft shadow, darker side edge below the face, gradient face with a top highlight.
    The face occupies exactly rect; the side and shadow extend `depth` pixels below it. Selecting a button animates it:
    the colour eases from the card colour to the selected colour, the face lifts a few pixels and the accent outline fades in.
    Returns (highlight 0..1, lift in px) so callers can animate their contents the same way."""
    x, y, w, h = rect
    st = _BTN_ANIM.get(rect)
    if st is None:                             # first sight of this button: no animation, start settled
        if len(_BTN_ANIM) > 300:
            _BTN_ANIM.clear()
        st = _BTN_ANIM[rect] = [1.0 if sel else 0.0, base, base]
    st[1 if sel else 2] = base
    target = 1.0 if sel else 0.0
    if abs(st[0] - target) < 0.002:
        st[0] = target
    else:
        st[0] += (target - st[0]) * (1.0 - math.exp(-_anim_clock[1] * ANIM_SPEED))   # frame-rate independent ease-out
        _anim_clock[2] = True
    e = st[0] * st[0] * (3.0 - 2.0 * st[0])                   # smoothstep: gentle start and stop
    hl = round(e * ANIM_STEPS) / ANIM_STEPS                   # fine steps: invisible, but keeps the gradient cache small
    base = _mix(st[2], st[1], hl)
    lift = int(round(LIFT_PX * hl)) if h >= 80 else 0     # only the big home tiles lift; list rows keep their text aligned
    y -= lift
    if XMB_UI[0]:                                              # XMB: flat translucent glass (no shadow or side edge), fading to the selected colour
        surf.blit(_glass_face(w, h, radius, _mix(_glass_tint(), SEL_BG, hl), 105 + int(70 * hl)), (x, y))
        if hl > 0:
            pygame.draw.rect(surf, _mix(TEXT, ACCENT, hl), (x, y, w, h), 2 if h < 80 else 3, border_radius=radius)
        return hl, lift
    side = _mix(base, (0, 0, 0), 0.55)
    sh = pygame.Surface((w, h + depth + 3), pygame.SRCALPHA)
    pygame.draw.rect(sh, (0, 0, 0, 70), (0, 3, w, h + depth), border_radius=radius)
    surf.blit(sh, (x, y + lift))
    pygame.draw.rect(surf, side, (x, y + depth, w, h), border_radius=radius)
    surf.blit(_btn_face(w, h, base, radius), (x, y))
    if hl > 0:
        pygame.draw.rect(surf, _mix(base, ACCENT, hl), (x, y, w, h), 2 if h < 80 else 3, border_radius=radius)
    return hl, lift


HOME_COLS = 2                                  # home grid: 2 columns x 3 rows (fits the square screen)
HOME_ITEMS = [("games", "Games"), ("recents", "Recents"), ("favorites", "Favorites"), ("tools", "Tools"), ("settings", "Settings"), ("power", "Power Off")]
# Tools screen: (id, name, description, launch target written to /run/rgos/launch; rgos-session maps the "@tool:" targets)
TOOLS = [("files", "File Manager", "Browse, copy, move, rename and delete files on the SD cards", "@view:files"),
         ("portmaster", "PortMaster", "Browse, install and update native Linux game ports", "@tool:portmaster"),
         ("retroarch", "RetroArch", "Open the RetroArch menu (cores, content, settings)", "@tool:retroarch")]
BOOT_ROOT = "/boot"                            # the RGOS FAT partition (partition 1), mounted read-write by rgos-init
# top-level items on the RGOS partition that the device needs to start; the file manager won't delete, rename or move them
BOOT_PROTECTED = ("SYSTEM", "SYSTEM.md5", "KERNEL", "KERNEL.md5", "extlinux", "dtbs", "device_trees", "boot.ini",
                  "rgos-data", "boot.scr", "Image", "uInitrd", "initrd", "initrd.img", "u-boot.bin", "idbloader.img",
                  "trust.img", "uboot.img", "rgos-initrd.cpio")


def fm_roots():
    """Storage the file manager may touch, in the order shown: RGOS (boot partition), the second SD card when it is
    mounted, and ROMS last."""
    roots = []
    if os.path.ismount(BOOT_ROOT):
        roots.append(BOOT_ROOT)
    sd = load_json("/etc/rgos/sdcard.json", {"enabled": True, "mountpoint": "/media/roms2"})
    mp = sd.get("mountpoint", "/media/roms2")
    if sd.get("enabled", True) and mp and os.path.ismount(mp):
        roots.append(mp)
    roots.append(ROMS_ROOT)
    return roots


def make_file_manager():
    return filemanager.FileManager(fm_roots(), labels={BOOT_ROOT: "RGOS", ROMS_ROOT: "ROMS"},
                                   protected={BOOT_ROOT: BOOT_PROTECTED, ROMS_ROOT: ("rgos-data",)})


FM_ROWS = 7 + DY // 44                          # visible rows in the file manager list
# rows of Settings, in order (ids)
SETTINGS_ROWS = ["theme", "launcher", "splash", "emulators", "bmap", "swapab", "wifi", "bluetooth", "ssh", "info"]
RECENT_MAX = 20

# Launchers (Settings > Launcher): the look of the home screen. "rgos" is the classic tile grid, "xmb" a PlayStation-style cross media bar
# (categories side by side, the items of the selected one listed below it). The choice is kept in state.json ("launcher").
LAUNCHERS = [("rgos", "RG OS", "Classic tile launcher"), ("xmb", "XMB", "PlayStation-style cross media bar")]
DEFAULT_LAUNCHER = LAUNCHERS[0][0]


def launcher_index(lid):
    for i, l in enumerate(LAUNCHERS):
        if l[0] == lid:
            return i
    return 0


def dd_options(kind):
    """Entries of a Settings dropdown as (id, name, swatch colours or None, description)."""
    if kind == "theme":
        return [(t[0], t[1], [t[2][k] for k in ("BG", "PANEL", "SEL_BG", "ACCENT", "TEXT")], "") for t in THEMES]
    return [(l[0], l[1], None, l[2]) for l in LAUNCHERS]


XMB_CATS = [("settings", "Settings"), ("games", "Games"), ("recents", "Recents"), ("favorites", "Favorites"), ("tools", "Tools"), ("power", "Power")]
XMB_START = 1                                  # the XMB opens on Games


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


def icon_favorites(c, cx, cy, col, bg):
    # Five-point star, centered on the home tile.
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = 29 if i % 2 == 0 else 13
        pts.append((int(cx + math.cos(ang) * rad), int(cy + math.sin(ang) * rad)))
    pygame.draw.polygon(c, col, pts)


def icon_power(c, cx, cy, col, bg):
    # Power symbol: a ring open at the top plus a vertical bar, drawn with round caps and centred on the tile.
    r, t = 24, 5
    cy += 3
    pts = [(cx + r * math.sin(math.radians(a)), cy - r * math.cos(math.radians(a))) for a in range(38, 323, 4)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        pygame.draw.line(c, col, (x1, y1), (x2, y2), t)
    for x, y in pts:
        pygame.draw.circle(c, col, (int(x), int(y)), t // 2)
    pygame.draw.line(c, col, (cx, cy - r - 4), (cx, cy - 4), t)
    pygame.draw.circle(c, col, (cx, cy - r - 4), t // 2)
    pygame.draw.circle(c, col, (cx, cy - 4), t // 2)


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


def icon_theme(c, cx, cy, col, bg):                     # paint palette
    pygame.draw.circle(c, col, (cx, cy), 28)
    for dx, dy in ((-13, -9), (3, -17), (17, -4)):
        pygame.draw.circle(c, bg, (cx + dx, cy + dy), 5)
    pygame.draw.circle(c, bg, (cx + 10, cy + 16), 8)


def icon_launcher(c, cx, cy, col, bg):                  # 2 x 2 grid of tiles
    for dx in (-27, 3):
        for dy in (-27, 3):
            pygame.draw.rect(c, col, (cx + dx, cy + dy, 24, 24), border_radius=6)


def icon_splash(c, cx, cy, col, bg):                    # screen with a play triangle
    pygame.draw.rect(c, col, (cx - 32, cy - 22, 64, 44), border_radius=8)
    pygame.draw.polygon(c, bg, [(cx - 8, cy - 12), (cx - 8, cy + 12), (cx + 14, cy)])


def icon_emulators(c, cx, cy, col, bg):                 # chip with pins
    pygame.draw.rect(c, col, (cx - 20, cy - 20, 40, 40), border_radius=6)
    pygame.draw.rect(c, bg, (cx - 10, cy - 10, 20, 20), border_radius=3)
    for i in (-1, 0, 1):
        pygame.draw.rect(c, col, (cx - 32, cy + i * 14 - 2, 10, 4))
        pygame.draw.rect(c, col, (cx + 22, cy + i * 14 - 2, 10, 4))
        pygame.draw.rect(c, col, (cx + i * 14 - 2, cy - 32, 4, 10))
        pygame.draw.rect(c, col, (cx + i * 14 - 2, cy + 22, 4, 10))


def icon_bmap(c, cx, cy, col, bg):                      # D-pad
    pygame.draw.rect(c, col, (cx - 10, cy - 30, 20, 60), border_radius=4)
    pygame.draw.rect(c, col, (cx - 30, cy - 10, 60, 20), border_radius=4)
    pygame.draw.circle(c, bg, (cx, cy), 5)


def icon_swapab(c, cx, cy, col, bg):                    # two opposite arrows
    pygame.draw.line(c, col, (cx - 28, cy - 12), (cx + 18, cy - 12), 5)
    pygame.draw.polygon(c, col, [(cx + 16, cy - 24), (cx + 34, cy - 12), (cx + 16, cy)])
    pygame.draw.line(c, col, (cx + 28, cy + 12), (cx - 18, cy + 12), 5)
    pygame.draw.polygon(c, col, [(cx - 16, cy), (cx - 34, cy + 12), (cx - 16, cy + 24)])


def icon_wifi(c, cx, cy, col, bg):
    for r in (14, 26, 38):
        pygame.draw.arc(c, col, (cx - r, cy + 16 - r, 2 * r, 2 * r), math.radians(45), math.radians(135), 5)
    pygame.draw.circle(c, col, (cx, cy + 16), 5)


def icon_bluetooth(c, cx, cy, col, bg):
    pts = [(cx - 14, cy - 12), (cx + 14, cy + 12), (cx, cy + 26), (cx, cy - 26), (cx + 14, cy - 12), (cx - 14, cy + 12)]
    pygame.draw.lines(c, col, False, pts, 5)


def icon_ssh(c, cx, cy, col, bg):                       # terminal window
    pygame.draw.rect(c, col, (cx - 34, cy - 24, 68, 48), 4, border_radius=6)
    pygame.draw.lines(c, col, False, [(cx - 20, cy - 10), (cx - 8, cy), (cx - 20, cy + 10)], 4)
    pygame.draw.line(c, col, (cx, cy + 10), (cx + 16, cy + 10), 4)


def icon_info(c, cx, cy, col, bg):
    pygame.draw.circle(c, col, (cx, cy), 28, 4)
    pygame.draw.circle(c, col, (cx, cy - 13), 4)
    pygame.draw.line(c, col, (cx, cy - 4), (cx, cy + 15), 5)


def icon_files(c, cx, cy, col, bg):                     # folder
    pygame.draw.rect(c, col, (cx - 32, cy - 22, 28, 12), border_radius=4)
    pygame.draw.rect(c, col, (cx - 32, cy - 14, 64, 40), border_radius=6)


ICONS = {"games": icon_games, "recents": icon_recents, "favorites": icon_favorites, "tools": icon_tools, "settings": icon_settings, "power": icon_power,
         "theme": icon_theme, "launcher": icon_launcher, "splash": icon_splash, "emulators": icon_emulators, "bmap": icon_bmap,
         "swapab": icon_swapab, "wifi": icon_wifi, "bluetooth": icon_bluetooth, "ssh": icon_ssh, "info": icon_info, "files": icon_files}

# ---- XMB launcher drawing ----------------------------------------------------------------------------------------------------
_XI = {}                                       # icon caches: white 96x96 originals and their scaled copies
_XIT = {}                                      # finished (tinted + faded) icons keyed by (kind, size, colour, alpha), blitted as they are
_XBG = {}                                      # cached background gradient
_XBG_RAW = [None]                              # the same gradient as raw RGB bytes: colour lookups without locking a surface
_XSILK = {}                                    # silk geometry that does not change from frame to frame (see xmb_bg)
_XFRAME = [None, -1.0]                         # the last rendered background frame and the time it was drawn
XMB_BG_INTERVAL = 1.0 / 30.0                   # the background is redrawn at most this often (it moves slowly; menu motion can run at 60 fps)


def xmb_icon(kind, size, color, alpha=1.0):
    """The icon `kind` as a size x size surface tinted `color` and faded by `alpha` (the shapes are drawn once, then scaled + cached;
    the tinted result is cached too, with the colour and fade rounded to steps of 4/255 that the eye cannot tell apart)."""
    ck = tuple(min(255, (int(v) + 2) & ~3) for v in color[:3])
    a8 = int(alpha * 255) if alpha < 0.99 else 255
    aq = 255 if a8 >= 255 else max(0, a8) & ~3
    key = (kind, size, ck, aq)
    t = _XIT.get(key)
    if t is not None:
        return t
    base = _XI.get(kind)
    if base is None:
        base = pygame.Surface((96, 96), pygame.SRCALPHA)
        ICONS[kind](base, 48, 48, (255, 255, 255, 255), (0, 0, 0, 0))
        _XI[kind] = base
    sc = _XI.get((kind, size))
    if sc is None:
        sc = _XI[(kind, size)] = pygame.transform.smoothscale(base, (size, size))
    t = sc.copy()
    t.fill(ck + (255,), special_flags=pygame.BLEND_RGBA_MULT)
    if aq < 255:
        t.set_alpha(aq)
    if len(_XIT) > 600:
        _XIT.clear()
    _XIT[key] = t
    return t


# PS3-style silk. Each bundle is a sheet of fine strands that sweeps across the screen, fans out and pinches together where it \"twists\":
# (height at the left edge, height at the right edge (both as a fraction of H), swell amplitude, swell wave number, swell speed,
#  max half-thickness, twist wave number, twist speed, twist phase, number of strands, brightness)
_XMB_SILK = ((0.68, 0.43, 16, 0.0085, 0.22, 48, 0.0060, 0.10, 0.4, 18, 0.55),
             (0.77, 0.58, 12, 0.0110, -0.18, 34, 0.0075, -0.12, 2.6, 12, 0.36))
_XMB_SPARKS = []                               # (x, y, drift speed, phase, size) of the twinkling dust, built once


def xmb_tint(x, y, k):
    """The background colour at (x, y) lifted toward the text colour by k (soft light shapes that follow the gradient)."""
    raw = _XBG_RAW[0]
    if raw is None:
        base = BG
    else:
        i = (max(0, min(H - 1, int(y))) * W + max(0, min(W - 1, int(x)))) * 3
        base = (raw[i], raw[i + 1], raw[i + 2])
    return _mix(base, TEXT, k)


def _xmb_silk_build(g):
    """Everything about the silk that only depends on the theme and the screen size: the x grid, the parts of the sine waves that do not
    depend on time (so a frame needs a handful of sin/cos instead of one per point), and the colour of every strand."""
    hl = XMB_SILK
    xs = list(range(0, W + 10, 10))
    wig = ([math.sin(x * 0.021) for x in xs], [math.cos(x * 0.021) for x in xs])
    bundles = []
    for yl, yr, amp, kf, sp, th, kt, st, ph, n, br in _XMB_SILK:
        yb, s1, k1, s2, k2, s3, k3 = [], [], [], [], [], [], []
        for x in xs:
            u = x / float(W)
            yb.append(H * (yl + (yr - yl) * u * u * (3 - 2 * u)))
            p1, p2, p3 = x * kf + ph, x * kf * 2.3, x * kt + ph
            s1.append(math.sin(p1)); k1.append(math.cos(p1))
            s2.append(math.sin(p2)); k2.append(math.cos(p2))
            s3.append(math.sin(p3)); k3.append(math.cos(p3))
        base = g.get_at((W // 3, min(H - 1, int(H * (yl + yr) / 2))))[:3]
        strands = []
        for k in range(n):                                     # the fine strands; the upper ones catch the most light
            f = k / float(n - 1) * 2.0 - 1.0
            strands.append((f, math.cos(k * 0.9), math.sin(k * 0.9),
                            _mix(base, hl, br * (0.30 + 0.45 * max(0.0, -f) + 0.15 * (1.0 - abs(f))))))
        bundles.append((yb, s1, k1, s2, k2, s3, k3, amp, sp, th, st, strands, _mix(base, hl, 0.12 * br / 0.55), _mix(base, hl, min(1.0, br * 1.5))))
    return xs, wig, bundles


def xmb_bg(c, t):
    """The XMB background: a diagonal gradient in the theme colours (dark top-left, glowing bottom-right), a sweeping sheet of silk
    strands that slowly shimmers and twists, and a little twinkling dust."""
    key = (XMB_TOP, XMB_LOW, XMB_SILK, W, H)
    g = _XBG.get(key)
    if g is None:
        _XBG.clear()
        _XBG_RAW[0] = None
        _XFRAME[0] = None
        gw, gh = 24, 16                         # a tiny gradient, scaled up smoothly (cheap and free of banding)
        small = pygame.Surface((gw, gh))
        top, low = XMB_TOP, XMB_LOW
        for gy in range(gh):
            for gx in range(gw):
                v = 0.30 * (gx + 0.5) / gw + 0.70 * (gy + 0.5) / gh
                pygame.draw.rect(small, _mix(top, low, v ** 1.15), (gx, gy, 1, 1))
        g = pygame.transform.smoothscale(small, (W, H))
        _XBG[key] = g
        _XSILK.clear()
        _XSILK["geo"] = _xmb_silk_build(g)
        try:
            _XBG_RAW[0] = (getattr(pygame.image, "tobytes", None) or pygame.image.tostring)(g, "RGB")
        except Exception:
            _XBG_RAW[0] = None
        if not _XMB_SPARKS:
            r = random.Random(11)
            for i in range(36):
                _XMB_SPARKS.append((r.uniform(0, W), (r.uniform(0.10, 0.98) if i % 3 else r.uniform(0.40, 0.85)) * H,
                                    r.uniform(3, 12), r.uniform(0, 6.28), 2 if r.random() < 0.8 else 3))
    fr = _XFRAME
    if fr[0] is not None and 0.0 <= t - fr[1] < XMB_BG_INTERVAL:   # drawn a moment ago: reuse it (menu animation can run at 60 fps)
        c.blit(fr[0], (0, 0))
        return
    if fr[0] is None:
        fr[0] = g.copy()
    else:
        fr[0].blit(g, (0, 0))
    fr[1] = t
    f = fr[0]
    xs, (sx0, cx0), bundles = _XSILK["geo"]
    cw, sw = math.cos(t * 0.35), math.sin(t * 0.35)
    S = [a * cw + b * sw for a, b in zip(sx0, cx0)]            # sin(x * 0.021 + t * 0.35) for every x (shared by all strands)
    C = [b * cw - a * sw for a, b in zip(sx0, cx0)]            # ... and its cosine
    for yb, s1, k1, s2, k2, s3, k3, amp, sp, th, st, strands, poly_col, edge_col in bundles:
        c1, d1 = math.cos(t * sp), math.sin(t * sp)
        c2, d2 = math.cos(t * sp * 0.8), math.sin(t * sp * 0.8)
        c3, d3 = math.cos(t * st), math.sin(t * st)
        amp2 = amp * 0.5
        rows = []
        for x, y0, a1, b1, a2, b2, a3, b3, sn, cs in zip(xs, yb, s1, k1, s2, k2, s3, k3, S, C):
            tw = 0.5 + 0.5 * (a3 * c3 + b3 * d3)
            h = th * (0.10 + 0.90 * tw * tw)                   # fans out, then pinches to a thin line
            rows.append((x, y0 + amp * (a1 * c1 + b1 * d1) + amp2 * (a2 * c2 - b2 * d2), h, 0.09 * h * sn, 0.09 * h * cs))
        upper = [(x, yc - h) for x, yc, h, _s, _c in rows]
        pygame.draw.polygon(f, poly_col, upper + [(x, yc + h) for x, yc, h, _s, _c in rows][::-1])
        for sf, sck, ssk, col in strands:
            pygame.draw.aalines(f, col, False, [(x, yc + h * sf + hs * sck + hc * ssk) for x, yc, h, hs, hc in rows])
        pygame.draw.lines(f, edge_col, False, upper, 2)        # lit upper edge
    for x0, y0, spd, ph, sz in _XMB_SPARKS:                    # twinkling dust
        x, y = (x0 + t * spd) % W, y0 + 4 * math.sin(t * 0.4 + ph)
        pygame.draw.rect(f, xmb_tint(x, y, 0.22 + 0.55 * (0.5 + 0.5 * math.sin(t * 1.6 + ph))), (int(x), int(y), sz, sz))
    c.blit(f, (0, 0))


def draw_xmb_home(c, f_row, f_small, cx_f, cat_i, items, vy, fade):
    """The XMB home: category icons along a horizontal bar (the selected one big, with its name), its items listed below.
    cx_f / vy are the animated (float) category and item positions, fade the 0..1 fade-in of the item column."""
    ax, bar_y, big, small, sp_x = 170, int(H * 0.26), 76, 50, 118
    sp, gap = 56 + max(0, H - 480) // 10, 16
    for i, (cid, cname) in enumerate(XMB_CATS):
        d = i - cx_f
        x = int(ax + d * sp_x + max(-1.0, min(1.0, d)) * 24)
        if x < -60 or x > W + 60:
            continue
        t = max(0.0, 1.0 - abs(d))
        size = int(small + (big - small) * t)
        if t > 0.05:
            pygame.draw.circle(c, xmb_tint(x, bar_y, 0.09 * t), (x, bar_y), int(size * 0.62))
        c.blit(xmb_icon(cid, size, _mix(DIM, TEXT, t), 0.5 + 0.5 * t), (x - size // 2, bar_y - size // 2))
        if t > 0.3:
            lab = f_row.render(cname, True, _mix(BG, TEXT, t))
            c.blit(lab, (x - lab.get_width() // 2, bar_y + big // 2 + 8))
    colx = int(ax + (cat_i - cx_f) * sp_x)
    y0 = bar_y + big // 2 + 8 + 26 + sp // 2 + 6
    for i, it in enumerate(items):
        d = i - vy
        if d < -1.0 or d > 7.0:
            continue
        y = y0 + d * sp + max(-1.0, min(1.0, d)) * gap
        if y > H - 40:
            continue
        sel_t = max(0.0, 1.0 - abs(d))
        al = (1.0 + min(0.0, d)) * fade                      # items above the selection fade out under the label
        if d > 0:
            al *= max(0.0, min(1.0, (H - 44 - y) / 30.0))   # ... and the ones below near the hint line
        if al <= 0.02:
            continue
        al *= 0.80 + 0.20 * sel_t
        isz = int(34 + 14 * sel_t)
        if sel_t > 0.05:
            pygame.draw.circle(c, xmb_tint(colx, y, 0.08 * sel_t * al), (colx, int(y)), int(isz * 0.62))
        c.blit(xmb_icon(it["icon"], isz, _mix(DIM, TEXT, sel_t), al), (colx - isz // 2, int(y) - isz // 2))
        tx = colx + 40
        lab = f_row.render(fit(f_row, it["label"], W - tx - 20), True, _mix(BG, _mix(DIM, TEXT, 0.40 + 0.60 * sel_t), al))
        c.blit(lab, (tx, int(y) - lab.get_height() // 2 - int(10 * sel_t)))
        if sel_t > 0.2 and it.get("sub"):
            sub = f_small.render(fit(f_small, it["sub"], W - tx - 20), True, _mix(BG, DIM, al * sel_t))
            c.blit(sub, (tx, int(y) + 3))


_XDD = [None, None]                            # key and finished panel of the last dropdown frame


def draw_xmb_dropdown(c, f_small, dd, opts):
    """Settings dropdown (Theme / Launcher) shown over the XMB: a translucent panel under the category bar.
    Browsing a theme previews it live, so the XMB gradient, silk and dust recolour behind the panel."""
    rh, top = 36, 150
    vis = max(1, min(len(opts), (H - 50 - top - 8) // rh))
    w, h = min(W - 60, 520), vis * rh + 12
    x, y = (W - w) // 2, min(top, H - 50 - h)
    first = min(max(0, dd["sel"] - vis // 2), len(opts) - vis)
    acc = tuple(ACCENT)[:3]
    key = (dd["sel"], dd["orig"], dd.get("kind"), first, vis, w, h, acc, tuple(TEXT), tuple(DIM), len(opts), f_small)
    if _XDD[0] == key:                                                      # nothing changed since the last frame: reuse the panel
        c.blit(_XDD[1], (x, y))
        return
    panel = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(panel, (0, 0, 0, 222), (0, 0, w, h), border_radius=12)
    pygame.draw.rect(panel, acc + (230,), (0, 0, w, h), 2, border_radius=12)
    for slot, ti in enumerate(range(first, first + vis)):
        ry, rsel = 6 + slot * rh, ti == dd["sel"]
        if rsel:
            pygame.draw.rect(panel, acc + (70,), (6, ry, w - 12, rh - 2), border_radius=8)
        oid, oname, oswatch, odesc = opts[ti]
        panel.blit(f_small.render(fit(f_small, oname + ("   (current)" if oid == dd["orig"] else ""), w - 180), True, TEXT if rsel else DIM), (18, ry + 8))
        if oswatch:
            for j, col in enumerate(oswatch):
                sx = w - 18 - (len(oswatch) - j) * 24
                pygame.draw.rect(panel, col, (sx, ry + 6, 20, 20), border_radius=5)
                pygame.draw.rect(panel, DIM, (sx, ry + 6, 20, 20), 1, border_radius=5)
        elif odesc:
            dt_ = f_small.render(fit(f_small, odesc, w - 200), True, DIM)
            panel.blit(dt_, (w - 18 - dt_.get_width(), ry + 8))
    if len(opts) > vis:                                                     # more-above / more-below markers
        if first > 0:
            pygame.draw.polygon(panel, acc, [(w - 8, 12), (w - 14, 18), (w - 2, 18)])
        if first + vis < len(opts):
            pygame.draw.polygon(panel, acc, [(w - 8, h - 12), (w - 14, h - 18), (w - 2, h - 18)])
    _XDD[0], _XDD[1] = key, panel
    c.blit(panel, (x, y))


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


def _uptime_text():
    try:
        sec = int(float(rd_file("/proc/uptime").split()[0]))
    except (IndexError, ValueError):
        return "n/a"
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    return ("%dd " % d if d else "") + "%dh %02dm" % (h, rem // 60)


def _mem_text():
    kb = {}
    for l in rd_file("/proc/meminfo").splitlines():
        parts = l.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            kb[parts[0]] = int(parts[1])
    if "MemTotal" not in kb:
        return "n/a"
    free = kb.get("MemAvailable", kb.get("MemFree", 0))
    return "%d / %d MB free" % (free // 1024, kb["MemTotal"] // 1024)


def _cpu_text():
    n = os.cpu_count() or 1
    model = ""
    for l in rd_file("/proc/cpuinfo").splitlines():
        if l.lower().startswith(("model name", "hardware")) and ":" in l:
            model = l.split(":", 1)[1].strip()
            break
    return ("%s x%d" % (model, n)) if model else "%d core%s" % (n, "" if n == 1 else "s")


def _temp_text():
    for path in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        try:
            return "%.0f C" % (int(rd_file(path).strip()) / 1000.0)
        except ValueError:
            continue
    return "n/a"


def _load_text():
    p = rd_file("/proc/loadavg").split()
    return " ".join(p[:3]) if len(p) >= 3 else "n/a"


def _rootfs_text():
    try:
        st = os.statvfs("/")
        return "%.1f / %.1f GB free" % (st.f_bavail * st.f_frsize / 1e9, st.f_blocks * st.f_frsize / 1e9)
    except OSError:
        return "n/a"


def settings_info():
    """Read-only system info lines for the Settings > Information screen (the page scrolls, so the list can be long)."""
    build = "".join(l[9:] for l in rd_file("/etc/rgos-release").splitlines() if l.startswith("BUILD_ID="))
    try:
        st = os.statvfs(ROMS_ROOT)
        space = "%.1f / %.1f GB free" % (st.f_bavail * st.f_frsize / 1e9, st.f_blocks * st.f_frsize / 1e9)
    except OSError:
        space = "not mounted"
    vol, bat, bri = read_volume(), read_battery(), read_brightness()
    return [("RG OS build", build or "unknown"), ("Device", DEVICE_ID), ("Kernel", os.uname().release),
            ("ROMS partition", space), ("SD card 2", card2_info()), ("Volume", "%d%%" % vol[0] if vol else "n/a"),
            ("Battery", ("%d%% %s" % (max(0, min(100, bat[0])), bat[1].lower())) if bat else "n/a"),
            ("Brightness", "%d%%" % bri[0] if bri else "n/a"),
            ("Buttons", "mapped" if os.path.exists(BTN_MAP) else "default codes"),
            ("A/B buttons", "swapped" if swap_ab_on() else "normal"),
            ("CPU", _cpu_text()), ("CPU temp", _temp_text()), ("Load average", _load_text()),
            ("Memory", _mem_text()), ("System storage", _rootfs_text()), ("Uptime", _uptime_text()),
            ("Resolution", "%dx%d" % (W, H))]


BTN_MAP = os.environ.get("RGOS_BTNMAP", "/var/lib/rgos/button-map.json")
SWAP_AB = os.environ.get("RGOS_SWAPAB", "/var/lib/rgos/swap-ab")     # flag file read by rgos-input


def restart_hub():
    """Restart the input hub (rgos-input) so a new button map / A-B swap takes effect now. There is no systemd in this
    image: stop the running hub with SIGTERM (it removes its virtual pad) and start a fresh one."""
    if os.environ.get("RGOS_WINDOWED") == "1":
        return

    def hubs():
        found = []
        for d in os.listdir("/proc"):
            if d.isdigit() and int(d) != os.getpid():
                try:
                    with open("/proc/%s/cmdline" % d, "rb") as f:
                        cmd = f.read().split(b"\0")
                except OSError:
                    continue
                if any(c.endswith(b"rgos-input") for c in cmd[:3]) and b"reset-buttons" not in cmd:
                    found.append(int(d))
        return found
    for pid in hubs():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    t0 = time.time()
    while hubs() and time.time() - t0 < 4:
        time.sleep(0.1)
    for pid in hubs():                                   # did not exit in time
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        lg = open("/var/log/rgos-input.log", "a")
    except OSError:
        lg = subprocess.DEVNULL
    subprocess.Popen(["python3", "/opt/rgos/rgos-input"], stdin=subprocess.DEVNULL, stdout=lg, stderr=lg,
                     start_new_session=True)


def swap_ab_on():
    return os.path.exists(SWAP_AB)


def set_swap_ab(on):
    try:
        if on:
            os.makedirs(os.path.dirname(SWAP_AB), exist_ok=True)
            open(SWAP_AB, "w").close()
        elif os.path.exists(SWAP_AB):
            os.unlink(SWAP_AB)
    except OSError:
        return False
    return True


class ButtonMap:
    """Guided button mapping. While the probe flag exists the hub (rgos-input) publishes the last raw key / hat / trigger
    press of every source device as "<n>\\t<device>\\t<type>\\t<code>\\t<sign>". We ask for one button at a time and
    store raw press -> canonical code in button-map.json, then restart the hub. Optional buttons are skipped when
    nothing is pressed for a few seconds.
    After the buttons come the analog sticks (only on devices whose hub publishes them, i.e. writes sticks.raw): first both
    sticks are left alone to measure their centre, then each one is rolled around its edge to measure its travel. Both are
    stored under "sticks" in button-map.json. A stick that is not moved is skipped, so a dead or missing stick never blocks setup."""
    PROMPTS = [("A", 0x131, False), ("B", 0x130, False), ("X", 0x133, False), ("Y", 0x134, False),
               ("L", 0x136, False), ("R", 0x137, False), ("SELECT", 0x13a, False), ("START", 0x13b, False),
               ("D-pad UP", 0x220, False), ("D-pad DOWN", 0x221, False), ("D-pad LEFT", 0x222, False),
               ("D-pad RIGHT", 0x223, False), ("MENU (home) button", 0x13c, True), ("L2 / left trigger", 0x138, True),
               ("R2 / right trigger", 0x139, True), ("L3 (click left stick)", 0x13d, True), ("R3 (click right stick)", 0x13e, True)]
    STICK_BTNS = (0x13d, 0x13e)                    # only asked on devices that have sticks (the hub publishes sticks.raw)
    TIMEOUT, OPT_TIMEOUT, GAP = 25.0, 5.0, 0.5
    STICKS = [("LEFT stick", (0x00, 0x01)), ("RIGHT stick", (0x03, 0x04))]    # ABS_X/ABS_Y and ABS_RX/ABS_RY
    CENTER_SETTLE, CENTER_TIME = 0.6, 2.0          # seconds: let go of the sticks, then measure the rest position
    STICK_IDLE, STICK_TIMEOUT = 8.0, 30.0          # seconds: skip a stick that is never moved / give up on one that is never finished
    REACH, MOVED = 0.5, 0.15                       # fraction of the declared half-range: "edge reached in this direction" / "stick was moved"
    MIN_SIDE = 0.3                                 # a side whose measured travel is below this keeps the declared range instead

    def __init__(self):
        self.flag, self.rawf = os.path.join(RUN_DIR, "keys.probe"), os.path.join(RUN_DIR, "keys.raw")
        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            os.unlink(self.rawf)
        except OSError:
            pass
        try:
            os.unlink(os.path.join(RUN_DIR, "sticks.raw"))             # no stale values from an earlier run
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
        self.failed_in = ""
        self.sticksf = os.path.join(RUN_DIR, "sticks.raw")
        self.has_sticks = False                         # decided once the hub is up (see update): L3 / R3 are only asked when True
        self.sv = {}                                    # axis code -> (value, min, max): the live raw stick values
        self.sticks = []                                # indices into STICKS of the sticks this device has
        self.cstick = 0                                 # which of them is being measured
        self.center, self.cal = {}, {}                  # axis -> measured centre / (centre, low, high)
        self.csamples = {}
        self.ext, self.reached, self.tgrow, self.moved = {}, set(), 0.0, False

    def stop(self):
        for f in (self.flag, self.rawf, self.sticksf):
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
        self.failed_in = self.state                     # remembered so resume() can carry on from the same place
        self.state, self.msg = "fail", msg
        self.stop()

    def can_resume(self):
        return self.state == "fail" and self.failed_in in ("wait", "prompt", "apply")

    def resume(self, now=None):
        """Carry on after a failed step: the buttons already mapped are kept and only the step that failed is asked again
        (instead of starting the whole wizard over)."""
        if not self.can_resume():
            return
        now = now or time.time()
        was = self.failed_in
        for f in (self.rawf, self.sticksf):
            try:
                os.unlink(f)
            except OSError:
                pass
        try:
            open(self.flag, "w").close()
        except OSError:
            pass
        self.msg, self.stamp, self.t0, self.tstate = "", None, now, now
        self.state = "apply" if was == "apply" else "wait"      # "wait" re-arms the hub, then asks the same button again

    def total(self):
        """Number of button prompts on this device (L3 / R3 only count where there are sticks)."""
        return len(self.PROMPTS) - (0 if self.has_sticks else len(self.STICK_BTNS))

    def _next(self, now):
        self.step += 1
        while self.step < len(self.PROMPTS) and self.PROMPTS[self.step][1] in self.STICK_BTNS and not self.has_sticks:
            self.step += 1                              # no sticks, so no stick clicks either
        if self.step < len(self.PROMPTS):
            self.state, self.tstate = "prompt", now
        else:
            self._start_sticks(now)

    def _read_sticks(self):
        """Refresh self.sv from the hub's sticks.raw: {axis code: (value, min, max)}."""
        try:
            with open(self.sticksf) as f:
                ax = json.load(f).get("ax") or {}
            self.sv = {int(k): (int(v[0]), int(v[1]), int(v[2])) for k, v in ax.items()}
        except (OSError, ValueError, TypeError, IndexError, AttributeError):
            self.sv = {}
        return self.sv

    def _start_sticks(self, now):
        """Buttons are done: go on to the sticks if the hub publishes any (it only does so on devices with analog sticks)."""
        self._read_sticks()
        self.sticks = [i for i, (_, axes) in enumerate(self.STICKS) if all(a in self.sv for a in axes)]
        if not self.sticks:
            self.state, self.tstate = "apply", now
            return
        self.cstick, self.center, self.csamples, self.cal = 0, {}, {a: [] for i in self.sticks for a in self.STICKS[i][1]}, {}
        self.state, self.tstate = "scenter", now

    def _begin_roll(self, now):
        """Measure the travel of the current stick: start every axis at its centre and wait for it to be pushed out."""
        axes = self.STICKS[self.sticks[self.cstick]][1]
        self.ext = {a: [self.center[a], self.center[a]] for a in axes}
        self.reached, self.moved, self.tgrow = set(), False, now
        self.state, self.tstate = "sroll", now

    def _next_stick(self, now):
        self.cstick += 1
        if self.cstick < len(self.sticks):
            self._begin_roll(now)
        else:
            self.state, self.tstate = "apply", now

    def _half(self, a):
        _, lo, hi = self.sv[a]
        return max((hi - lo) / 2.0, 1.0)

    def _roll_update(self, now):
        name, axes = self.STICKS[self.sticks[self.cstick]]
        if any(a not in self.sv for a in axes):
            return
        for a in axes:
            v, c, half = self.sv[a][0], self.center[a], self._half(a)
            e = self.ext[a]
            if v < e[0] - 0.01 * half or v > e[1] + 0.01 * half:
                self.tgrow = now                        # still reaching further out
            e[0], e[1] = min(e[0], v), max(e[1], v)
            for sign, key in ((-1, (a, -1)), (1, (a, 1))):
                if (v - c) * sign >= self.REACH * half:
                    self.reached.add(key)
            if abs(v - c) >= self.MOVED * half:
                self.moved = True
        dirs = sum(1 for a in axes for sg in (-1, 1) if (self.ext[a][1] - self.center[a] if sg > 0 else self.center[a] - self.ext[a][0]) >= self.MOVED * self._half(a))
        if self.moved and dirs >= 2 and now - self.tgrow >= (1.0 if len(self.reached) >= 4 else 3.0):
            for a in axes:                              # a side that was never pushed out far enough keeps the declared range
                c, half, (_, lo, hi) = self.center[a], self._half(a), self.sv[a]
                e_lo = self.ext[a][0] if c - self.ext[a][0] >= self.MIN_SIDE * half else lo
                e_hi = self.ext[a][1] if self.ext[a][1] - c >= self.MIN_SIDE * half else hi
                self.cal[a] = (c, e_lo, e_hi)
            return self._next_stick(now)
        idle = now - self.tstate
        if (not self.moved and idle > self.STICK_IDLE) or idle > self.STICK_TIMEOUT:
            self.skipped.append(name.split(" ")[0].lower() + " stick")
            self._next_stick(now)

    def stick_view(self):
        """For the wizard screen: (title of the stick being measured, x, y in -1..1 around its centre, set of directions reached
        as (axis index 0/1, sign)) while a stick step is running, else None."""
        if self.state not in ("scenter", "sroll") or not self.sticks:
            return None
        name, axes = self.STICKS[self.sticks[min(self.cstick, len(self.sticks) - 1)]]
        if self.state == "scenter":
            name, axes = "both sticks", self.STICKS[self.sticks[0]][1]
        if any(a not in self.sv for a in axes):
            return name, 0.0, 0.0, set()
        def norm(a):
            v, lo, hi = self.sv[a]
            return max(-1.0, min(1.0, (v - (self.center.get(a, (lo + hi) / 2.0))) / self._half(a)))
        reached = {(axes.index(a), sg) for (a, sg) in self.reached if a in axes}
        return name, norm(axes[0]), norm(axes[1]), reached

    def update(self):
        now = time.time()
        if self.state in ("done", "fail"):
            return
        if self.state == "apply":
            try:
                os.makedirs(os.path.dirname(BTN_MAP), exist_ok=True)
                out = {"keys": self.keys}
                if self.cal:
                    out["sticks"] = {str(a): [round(v, 1) for v in cal] for a, cal in self.cal.items()}
                with open(BTN_MAP + ".tmp", "w") as f:
                    json.dump(out, f)
                os.replace(BTN_MAP + ".tmp", BTN_MAP)
                set_swap_ab(False)              # a fresh mapping defines A and B exactly as pressed: no extra swap on top
                restart_hub()
            except Exception as e:
                return self._fail("Could not save: %s" % e)
            self.stop()
            self.state = "done"
            self.msg = "Saved %d buttons%s%s. Press buttons in the menu to test them." % (
                len(self.keys), (" and %d stick%s" % (len(self.cal) // 2, "" if len(self.cal) == 2 else "s")) if self.cal else "",
                (" (skipped: %s)" % ", ".join(self.skipped)) if self.skipped else "")
            return
        if self.state in ("scenter", "sroll"):
            self._read_sticks()
            if self.state == "scenter":                 # sticks at rest: take the median of what they report as their centre
                if now - self.tstate >= self.CENTER_SETTLE:
                    for a, lst in self.csamples.items():
                        if a in self.sv:
                            lst.append(self.sv[a][0])
                if now - self.tstate >= self.CENTER_TIME:
                    for a, lst in self.csamples.items():
                        self.center[a] = sorted(lst)[len(lst) // 2] if lst else (self.sv[a][1] + self.sv[a][2]) / 2.0 if a in self.sv else 0
                    self._begin_roll(now)
            else:
                self._roll_update(now)
            return
        r = self._read()
        label, canon, optional = self.PROMPTS[min(self.step, len(self.PROMPTS) - 1)]
        if self.state == "wait":                        # give the hub a moment, then start
            if now - self.t0 > 1.0:
                self.has_sticks = any(all(a in self._read_sticks() for a in axes) for _, axes in self.STICKS)
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
            return ["Starting...", "Do not press anything yet"]
        if self.state == "prompt":
            label, _, optional = self.PROMPTS[self.step]
            return ["Press %s" % label, "(wait a few seconds to skip: this one is optional)" if optional else ""]
        if self.state == "scenter":
            return ["Let go of both sticks", "Leave them centred for a moment"]
        if self.state == "sroll":
            return ["Roll the %s around" % self.STICKS[self.sticks[self.cstick]][0], "Push it to the edge and go all the way round, slowly"]
        if self.state == "apply":
            return ["Applying...", ""]
        if self.state == "fail" and self.can_resume():
            return [self.msg, "Nothing is lost: only this step is asked again"]
        return [self.msg]


VOLUME_FILE = os.environ.get("RGOS_VOLUME_FILE", os.path.join(RUN_DIR, "volume"))


CALIB_DONE = os.environ.get("RGOS_CALIBDONE", "/var/lib/rgos/calibrated")   # exists once the forced first-run setup was completed


def calibration_needed():
    """True on the first start of a fresh install: the launcher then forces the button mapping.
    An install that already has its calibration files (updated from an older version) is marked done without asking."""
    if os.path.exists(CALIB_DONE):
        return False
    if os.path.exists(BTN_MAP):
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
    """Return (percent, status), preferring the live sysfs gauge over the optional daemon cache.

    The RG Rotate doesn't ship an rgos-hw device config, so relying only on /run/rgos/battery
    made Settings > Information report n/a even when Linux exposed the battery gauge.
    """
    # Read Linux's live power_supply interface first. This works independently of rgos-hw.
    try:
        supplies = sorted(glob.glob("/sys/class/power_supply/*"))
        batteries = [p for p in supplies if rd_file(os.path.join(p, "type")).strip().lower() == "battery"]
        batteries.sort(key=lambda p: ("batt" not in os.path.basename(p).lower(), p))
        for node in batteries:
            raw = rd_file(os.path.join(node, "capacity")).strip().rstrip("%")
            if raw.lstrip("+-").isdigit():
                pct = max(0, min(100, int(raw)))
                status = rd_file(os.path.join(node, "status")).strip().replace("_", " ") or "Unknown"
                return pct, status
    except (OSError, ValueError):
        pass

    # Fallback for devices whose battery is reported through rgos-hw.
    try:
        with open(BATT_FILE) as f:
            pct, status, stamp = f.read().split()[:3]
        if time.time() - int(float(stamp)) > 60:
            return None
        pct = max(0, min(100, int(pct.rstrip("%"))))
        status = status.replace("_", " ").strip() or "Unknown"
        return pct, status
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


# Launcher background music. Tracks can be bundled in /opt/rgos/music or added by the user
# to the ROMS partition at /media/roms/music. Files are shuffled without repeating until
# the current playlist is exhausted; unsupported/corrupt tracks are skipped gracefully.
MUSIC_DIRS = (os.path.join(os.path.dirname(os.path.abspath(__file__)), "music"),
              os.path.join(ROMS_ROOT, "music"))
MUSIC_EXTS = (".mp3", ".ogg", ".oga", ".wav", ".flac", ".mod", ".xm", ".it", ".s3m")
_music_playlist = []
_music_last = None

def _music_tracks():
    tracks = []
    for root in MUSIC_DIRS:
        if not os.path.isdir(root):
            continue
        for base, _dirs, names in os.walk(root):
            for name in names:
                if name.lower().endswith(MUSIC_EXTS):
                    path = os.path.join(base, name)
                    if path not in tracks:
                        tracks.append(path)
    return tracks

def _start_launcher_music():
    global _music_playlist, _music_last
    tracks = _music_tracks()
    if not tracks:
        return
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init(22050, -16, 2, 2048)
        _music_playlist = tracks[:]
        import random
        random.shuffle(_music_playlist)
        if len(_music_playlist) > 1 and _music_playlist[0] == _music_last:
            _music_playlist.append(_music_playlist.pop(0))
        _play_next_launcher_track()
        log("launcher music: %d track(s) found" % len(tracks))
    except Exception as e:
        log("launcher music unavailable: %s" % e)

def _play_next_launcher_track():
    global _music_playlist, _music_last
    while _music_playlist:
        path = _music_playlist.pop(0)
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(0.55)
            pygame.mixer.music.play()
            _music_last = path
            log("launcher music playing: %s" % path)
            return
        except Exception as e:
            log("launcher music skipped %s: %s" % (path, e))
    # Re-scan so custom tracks copied to the SD card while the launcher is running can be found.
    tracks = _music_tracks()
    if tracks:
        import random
        _music_playlist = tracks[:]
        random.shuffle(_music_playlist)
        if len(_music_playlist) > 1 and _music_playlist[0] == _music_last:
            _music_playlist.append(_music_playlist.pop(0))
        while _music_playlist:
            path = _music_playlist.pop(0)
            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.set_volume(0.55)
                pygame.mixer.music.play()
                _music_last = path
                log("launcher music playing: %s" % path)
                return
            except Exception as e:
                log("launcher music skipped %s: %s" % (path, e))


def main():
    windowed = os.environ.get("RGOS_WINDOWED") == "1"
    ensure_folders()
    # RGOS_VIDEO=fb: use SDL's dummy driver for the off-screen canvas and draw into /dev/fb0 through fbout.
    if not windowed and os.environ.get("RGOS_VIDEO") == "fb":
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.init()
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
        mk = lambda s: CFont(pygame.font.Font(fpath, s))
    else:
        mk = lambda s: CFont(pygame.font.Font(None, int(s * 1.3)))
    f_logo, f_big, f_row, f_small, f_tiny = mk(30), mk(22), mk(19), mk(14), mk(11)

    inp = Input()
    all_games = scan_roms()
    _gcache = [None, {}]

    def games_of(sid):                         # per-system lists, rebuilt only when the ROM scan is replaced (was O(all games) per frame)
        if _gcache[0] is not all_games:
            by = {}
            for g in all_games:
                by.setdefault(g["sid"], []).append(g)
            _gcache[0], _gcache[1] = all_games, by
        return _gcache[1].get(sid, [])
    vsys = visible_systems(all_games)
    NO_SYSTEM = ("", "", "", (), "")           # placeholder while no system has any games
    state = load_state()
    theme_id = apply_theme(state.get("theme"))
    launcher_id = LAUNCHERS[launcher_index(state.get("launcher"))][0]

    _rcache = [None, None, []]

    def recent_games():
        rec = tuple(state.get("recent", []))
        if _rcache[0] is not all_games or _rcache[1] != rec:
            by_path = {g["path"]: g for g in all_games}
            _rcache[0], _rcache[1], _rcache[2] = all_games, rec, [by_path[p] for p in rec if p in by_path]
        return _rcache[2]

    favorite_paths = set(load_favorites())

    _fbp = [None, {}]                          # path -> game, rebuilt only when the ROM scan is replaced (was O(all games) per frame)

    def favorite_games():
        if _fbp[0] is not all_games:
            _fbp[0], _fbp[1] = all_games, {g["path"]: g for g in all_games}
        by_path = _fbp[1]
        return [by_path[p] for p in load_favorites() if p in by_path]

    def settings_text():
        """Title and one-line status of every Settings row (shown in the Settings list and under the XMB's Settings category)."""
        wst = wifictl.status()
        spl = {"on": "On: video and sound", "silent": "On: video only", "off": "Off"}[splash.mode_of(state)]
        return {"theme": ("Theme", "%s   (%d of %d)   A opens the list" % (THEMES[theme_index(theme_id)][1], theme_index(theme_id) + 1, len(THEMES))),
                "launcher": ("Launcher", "%s   (%d of %d)   A opens the list" % (LAUNCHERS[launcher_index(launcher_id)][1], launcher_index(launcher_id) + 1, len(LAUNCHERS))),
                "splash": ("Startup splash", spl + "   (plays once at power on)   X previews"),
                "emulators": ("Emulators", "Choose the RetroArch core used for each system"),
                "bmap": ("Map buttons", "Teach RG OS which physical button is A, B, D-pad, ... (if buttons are wrong)"),
                "swapab": ("Swap A / B buttons", ("ON" if swap_ab_on() else "off") + "   A toggles it (use if A and B act the wrong way round)"),
                "wifi": ("Wi-Fi", wifictl.status_text(wst)),
                "bluetooth": ("Bluetooth", {None: "Pair controllers, keyboards and headsets", True: "On   A opens the device list", False: "Off   A opens the device list"}[btm.on]),
                "ssh": ("SSH / file transfer", ("On: ssh or sftp root@%s" % wst["ip"] if wst["ip"] else "On: connect Wi-Fi to use it") if ssh_info()["running"] else "Off   A opens it"),
                "info": ("Information", "Build, kernel, storage, battery, volume and input status")}

    def _xmb_items(cat):
        """The vertical list under one XMB category: dicts with label, sub (second line), icon id and act (what A does, see the input code)."""
        if cat == "settings":
            txt = settings_text()
            return [{"label": txt[r][0], "sub": txt[r][1], "icon": r, "act": ("setting", i)} for i, r in enumerate(SETTINGS_ROWS)]
        if cat == "games":
            if not vsys:
                return [{"label": "No games found", "sub": "Copy ROMs to the ROMS card, then press X to rescan", "icon": "games", "act": None}]
            return [{"label": sy[4], "sub": "%d %s" % (len(games_of(sy[0])), "port" if sy[0] == "ports" else "game") + ("" if len(games_of(sy[0])) == 1 else "s"),
                     "icon": "games", "act": ("system", i)} for i, sy in enumerate(vsys)]
        if cat in ("recents", "favorites"):
            gl = recent_games() if cat == "recents" else favorite_games()
            if not gl:
                return [{"label": "No recent games" if cat == "recents" else "No favorites yet",
                         "sub": "Games you play show up here" if cat == "recents" else "Press Y on a game to add it", "icon": cat, "act": None}]
            return [{"label": g["title"], "sub": sysname.get(g["sid"], g["tag"]), "icon": "games", "act": ("launch", g)} for g in gl]
        if cat == "tools":
            return [{"label": t[1], "sub": t[2], "icon": "files" if t[3] == "@view:files" else "tools", "act": ("tool", t[3])} for t in TOOLS]
        return [{"label": "Power Off", "sub": "Shut down RG OS", "icon": "power", "act": ("power",)}]

    _xic = {}                                  # category -> (time built, what it was built from, items)

    def xmb_items(cat, fresh=False):
        """_xmb_items() for the frame loop: the list is rebuilt at most 4 times a second (building it reads status files and the favorites
        list, which was done every frame). The button handler passes fresh=True so that an action always uses the current list."""
        key, now = (theme_id, launcher_id, id(all_games), id(vsys)), time.monotonic()
        hit = _xic.get(cat)
        if not fresh and hit is not None and hit[1] == key and now - hit[0] < 0.25:
            return hit[2]
        items = _xic[cat] = (now, key, _xmb_items(cat))
        return items[2]

    # screens: home -> systems/games, recents, favorites, tools and settings
    view, home_idx, sys_idx, idx_by = "home", 0, 0, {}
    bmap = None
    set_idx, tool_idx, kb = 0, 0, None
    dd = None                                  # Settings dropdown (Theme / Launcher): None (closed) or {"kind", "sel": index, "orig": id to restore on cancel}
    xmb_cat, xmb_sel, xmb_cx, xmb_vy, xmb_fade = XMB_START, {}, float(XMB_START), 0.0, 1.0     # XMB: category, item per category, animated positions
    xmb_ret = [False]                          # True while a screen opened from the XMB is showing: B goes back to the XMB, not to the parent list
    pending_acts = []                          # buttons queued by the program itself (e.g. "A" after jumping from the XMB to a Settings row)
    sysname = {sy[0]: sy[4] for sy in SYSTEMS}

    def back_to(parent):
        """Where B leads from a sub screen: the XMB when it was opened from there, else its parent screen."""
        if xmb_ret[0]:
            xmb_ret[0] = False
            return "home"
        return parent

    def dd_set(kind, oid):
        """Make the theme / launcher with this id the active one (used for the live preview, confirm and cancel of the dropdown)."""
        nonlocal theme_id, launcher_id
        if kind == "theme":
            theme_id = apply_theme(oid)
        else:
            launcher_id = LAUNCHERS[launcher_index(oid)][0]
    fm, fm_pop = None, None                    # file manager (Tools > File Manager) and its popup (menu / confirm / info)
    emu_idx = 0
    info_scroll, info_pos = 0, 0.0           # Settings > Information: first visible row, and the smoothly animated pixel offset
    wconf, wscan, wf_idx, wf_msg, wf_msg_until, wf_pending = wifictl.load_conf(), wifictl.Scanner(), 0, "", 0.0, ("", False)
    btm, bt_idx = btctl.Manager(), 0           # Settings > Bluetooth (worker thread; rows, power state, messages)
    btm.refresh()
    ssh_cache, ssh_msg_until = (0.0, sshctl.info()), 0.0

    def ssh_info():
        """sshctl.info() at most once a second (it reads a few small files)."""
        nonlocal ssh_cache
        if time.time() - ssh_cache[0] > 1.0:
            ssh_cache = (time.time(), sshctl.info())
        return ssh_cache[1]

    def ssh_toggle():
        nonlocal ssh_cache, ssh_msg_until
        ssh_msg_until = time.time() + 30
        def job_():
            nonlocal ssh_cache, ssh_msg_until
            sshctl.set_enabled(not sshctl.enabled())
            ssh_cache, ssh_msg_until = (0.0, ssh_cache[1]), 0.0
        threading.Thread(target=job_, daemon=True).start()

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
    rows, list_x, list_w, top_y, row_h = 8 + DY // 44, 20, 450 + DX, 84, 44
    PH = 352 + DY                              # height of the big panels (side card, settings panels)
    clock = pygame.time.Clock()
    rd_vol, rd_bri, rd_bat = throttled(read_volume, 0.1), throttled(read_brightness, 0.1), throttled(read_battery, 10.0)
    last_act, clk_at, clk = time.monotonic(), 0, ""
    IDLE_VIEWS = ("home", "systems", "games", "recents", "tools", "settings")
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

    # Start random background music only after the splash has finished and released audio.
    _start_launcher_music()

    # First start of a fresh install: force the guided button mapping before the launcher can be used. A failed attempt
    # restarts by itself; after 3 failures MENU may skip it (so a broken input hub can never lock the device out). The
    # marker file is written only when the setup is finished.
    forced = calibration_needed()
    forced_fails, forced_t = 0, None
    if forced:
        bmap, view = ButtonMap(), "bmap"

    def forced_next():
        nonlocal forced, forced_fails, forced_t, view
        forced_fails, forced_t = 0, None
        forced, view = False, "home"
        mark_calibrated()

    blanked = False
    while code is None:
        if os.path.exists(SCREENOFF_FILE):      # asleep (rgos-hw): one black frame (even if the backlight cannot be switched), then idle
            inp.poll()                          # drains input; everything pressed in the dark is ignored
            if not blanked:
                canvas.fill((0, 0, 0))
                present()
                blanked = True
            pygame.time.wait(120)
            continue
        blanked = False
        if pygame.mixer.get_init() and not pygame.mixer.music.get_busy() and (_music_playlist or _music_tracks()):
            _play_next_launcher_track()
        _acts = inp.poll()
        if pending_acts:
            _acts, pending_acts = pending_acts + list(_acts), []
        if _acts:
            last_act = time.monotonic()
        if view == "home":
            xmb_ret[0] = False
        _acts = list(_acts)
        for _ai, a in enumerate(_acts):
            if forced:                         # first-run setup: buttons only feed the wizard (and MENU skips after 3 failures)
                if a == "quit":
                    code = EXIT_POWER
                elif a == "menu" and forced_fails >= 3:
                    bmap.stop()
                    forced_next()
                continue
            if view in ("games", "recents", "favorites"):
                key = "\0" + view if view in ("recents", "favorites") else (vsys[sys_idx][0] if vsys else "")
                games = recent_games() if view == "recents" else (favorite_games() if view == "favorites" else games_of(vsys[sys_idx][0] if vsys else ""))
                n, idx = len(games), idx_by.get(key, 0)
            if a == "quit":
                code = EXIT_POWER
            elif dd is not None:               # a Settings dropdown (Theme / Launcher) is open, in the Settings list or on the XMB: it takes all buttons
                opts = dd_options(dd["kind"])
                if a in ("up", "down"):
                    dd["sel"] = (dd["sel"] + (1 if a == "down" else -1)) % len(opts)
                    dd_set(dd["kind"], opts[dd["sel"]][0])            # live preview while browsing
                elif a == "a":
                    dd_set(dd["kind"], opts[dd["sel"]][0])
                    state[dd["kind"]] = opts[dd["sel"]][0]
                    save_state(state)
                    dd = None
                elif a in ("b", "menu"):
                    dd_set(dd["kind"], dd["orig"])                    # cancel: put the old one back
                    dd = None
            elif a == "menu":                  # MENU / home button: back to the home screen
                if view in ("systems", "games", "recents", "favorites", "tools", "settings", "wifi", "bt", "ssh", "info", "files"):
                    view = "home"
                elif view == "bmap" and bmap and bmap.state in ("done", "fail"):
                    view = "home"
            elif a == "x" and view in ("home", "systems", "games", "recents", "favorites"):
                cur_sid = vsys[sys_idx][0] if vsys else None
                all_games = scan_roms()        # rescan
                vsys = visible_systems(all_games)
                sys_idx = next((i for i, sy in enumerate(vsys) if sy[0] == cur_sid), min(sys_idx, max(0, len(vsys) - 1)))
                if view == "games" and not any(sy[0] == cur_sid for sy in vsys):
                    view = "home" if launcher_id == "xmb" else "systems"           # the system lost its last game
                elif view in ("games", "recents", "favorites"):
                    n = len(recent_games() if view == "recents" else (favorite_games() if view == "favorites" else games_of(vsys[sys_idx][0] if vsys else "")))
                    idx_by[key] = min(idx, max(0, n - 1))
            elif view == "home" and launcher_id == "xmb":      # XMB: left/right = category, up/down = item, A = open
                cat = XMB_CATS[xmb_cat][0]
                items = xmb_items(cat, True)
                cur = min(xmb_sel.get(cat, 0), len(items) - 1)
                if a in ("left", "right", "l", "r"):
                    new = max(0, min(len(XMB_CATS) - 1, xmb_cat + (1 if a in ("right", "r") else -1)))
                    if new != xmb_cat:
                        xmb_cat, xmb_fade = new, 0.0
                        xmb_vy = float(xmb_sel.get(XMB_CATS[new][0], 0))
                elif a in ("up", "down"):
                    xmb_sel[cat] = max(0, min(len(items) - 1, cur + (1 if a == "down" else -1)))
                elif a == "y" and items and items[cur]["act"] and items[cur]["act"][0] == "setting" and SETTINGS_ROWS[items[cur]["act"][1]] == "splash":
                    play_splash("silent" if splash.mode_of(state) == "silent" else "on")        # Y previews the startup splash (X is the rescan here)
                elif a == "a" and items and items[cur]["act"]:
                    act = items[cur]["act"]
                    if act[0] == "system":
                        sys_idx, view = act[1], "games"
                    elif act[0] == "launch":
                        code = launch(act[1]["path"], act[1])
                    elif act[0] == "tool":
                        if act[1] == "@view:files":
                            fm, fm_pop, view = make_file_manager(), None, "files"
                            xmb_ret[0] = True
                        else:
                            code = launch(act[1])
                    elif act[0] == "setting":      # handled right here on the XMB (the classic Settings list is never shown)
                        row = SETTINGS_ROWS[act[1]]
                        if row == "theme":         # A opens a dropdown over the XMB, the colours preview live
                            dd = {"kind": "theme", "sel": theme_index(theme_id), "orig": theme_id}
                        elif row == "launcher":
                            dd = {"kind": "launcher", "sel": launcher_index(launcher_id), "orig": launcher_id}
                        elif row == "splash":      # A steps on -> silent -> off
                            state["splash"] = splash.MODES[(splash.MODES.index(splash.mode_of(state)) + 1) % len(splash.MODES)]
                            save_state(state)
                        elif row == "swapab":      # exchange A and B for the menu AND the emulators (done in the input hub)
                            set_swap_ab(not swap_ab_on())
                            restart_hub()
                        else:                      # Emulators / Wi-Fi / Bluetooth / SSH / Information / Map buttons: a full-screen page, B returns to the XMB
                            set_idx, view = act[1], "settings"
                            xmb_ret[0] = True
                            _acts.insert(_ai + 1, "a")     # press A on that row right now (same frame, so the Settings list is never drawn)
                    elif act[0] == "power":
                        code = EXIT_POWER
            elif view == "home":               # HOME_COLS-column home grid
                col, row = home_idx % HOME_COLS, home_idx // HOME_COLS
                nrows = (len(HOME_ITEMS) + HOME_COLS - 1) // HOME_COLS
                if a == "left":
                    home_idx = row * HOME_COLS + (col - 1) % HOME_COLS
                elif a == "right":
                    home_idx = row * HOME_COLS + (col + 1) % HOME_COLS
                elif a in ("up", "down"):
                    candidate = ((row + (1 if a == "down" else -1)) % nrows) * HOME_COLS + col
                    home_idx = candidate if candidate < len(HOME_ITEMS) else home_idx
                elif a == "a":
                    view = HOME_ITEMS[home_idx][0]
                    if view == "power":
                        code = EXIT_POWER
                    elif view == "games":
                        view = "systems"       # Games -> consoles -> games
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
            elif view in ("games", "recents", "favorites"):
                if a == "b":
                    view = "systems" if view == "games" and launcher_id != "xmb" else "home"
                elif n and a == "down":
                    idx_by[key] = (idx + 1) % n
                elif n and a == "up":
                    idx_by[key] = (idx - 1) % n
                elif n and a in ("r", "right"):
                    idx_by[key] = min(n - 1, idx + rows)
                elif n and a in ("l", "left"):
                    idx_by[key] = max(0, idx - rows)
                elif a == "y" and n and view == "games":
                    path = games[idx]["path"]
                    if path in favorite_paths:
                        favorite_paths.discard(path)
                        action = "Removed from favorites"
                    else:
                        favorite_paths.add(path)
                        action = "Added to favorites"
                    if save_favorites(favorite_paths):
                        log("%s: %s" % (action, path))
                    else:
                        favorite_paths = set(load_favorites())
                elif a == "a" and n:
                    code = launch(games[idx]["path"], games[idx])
            elif view == "tools":
                if a in ("up", "down"):
                    tool_idx = (tool_idx + (1 if a == "down" else -1)) % len(TOOLS)
                elif a == "b":
                    view = "home"
                elif a == "a":
                    tgt = TOOLS[tool_idx][3]
                    if tgt == "@view:files":
                        fm, fm_pop, view = make_file_manager(), None, "files"
                    else:
                        code = launch(tgt)
            elif view == "files":
                if fm.busy:
                    pass                       # a copy / delete is running: ignore buttons until it is done
                elif fm_pop:
                    kind = fm_pop["kind"]
                    if kind == "info":
                        if a in ("a", "b"):
                            fm_pop = None
                    elif kind == "confirm":
                        if a in ("left", "right", "up", "down"):
                            fm_pop["sel"] = 1 - fm_pop["sel"]
                        elif a == "b":
                            fm_pop = None
                        elif a == "a":
                            if fm_pop["sel"] == 0:
                                fm.delete()
                            fm_pop = None
                    else:
                        if a in ("up", "down"):
                            fm_pop["sel"] = (fm_pop["sel"] + (1 if a == "down" else -1)) % len(fm_pop["items"])
                        elif a == "b":
                            fm_pop = None
                        elif a == "a":
                            act = fm_pop["items"][fm_pop["sel"]][0]
                            fm_pop = None
                            if act == "copy":
                                fm.copy()
                            elif act == "cut":
                                fm.cut()
                            elif act == "paste":
                                fm.paste()
                            elif act == "rename":
                                kb, view = Kbd("fm_rename", "Rename", fm.current()["name"], False), "kbd"
                            elif act == "mkdir":
                                kb, view = Kbd("fm_mkdir", "New folder name", "", False), "kbd"
                            elif act == "delete":
                                fm_pop = {"kind": "confirm", "sel": 1, "n": len(fm.targets())}
                            elif act == "info":
                                fm_pop = {"kind": "info", "lines": fm.info()}
                else:
                    fm.msg = ""
                    if a in ("up", "down"):
                        fm.wrap_cursor(1 if a == "down" else -1)
                    elif a in ("l", "left"):
                        fm.move_cursor(-FM_ROWS)
                    elif a in ("r", "right"):
                        fm.move_cursor(FM_ROWS)
                    elif a == "a":
                        if not fm.open_current() and fm.current():
                            fm_pop = {"kind": "info", "lines": fm.info()}
                    elif a == "b":
                        if not fm.go_up():
                            view = back_to("tools")
                    elif a == "y":
                        fm.toggle_mark()
                    elif a == "x":
                        items = []
                        if fm.cwd is not None:
                            if fm.targets():
                                items += [("copy", "Copy"), ("cut", "Cut")]
                            if fm.can_paste():
                                items.append(("paste", "Paste here (%d)" % len(fm.clip)))
                            if fm.current() and not fm.marked:
                                items.append(("rename", "Rename"))
                            items.append(("mkdir", "New folder"))
                            if fm.targets():
                                items.append(("delete", "Delete" + (" (%d)" % len(fm.marked) if fm.marked else "")))
                        if fm.current():
                            items.append(("info", "Details"))
                        if items:
                            fm_pop = {"kind": "menu", "items": items, "sel": 0}
            elif view == "settings":
                if a in ("up", "down"):
                    set_idx = (set_idx + (1 if a == "down" else -1)) % len(SETTINGS_ROWS)
                elif a in ("left", "right") and SETTINGS_ROWS[set_idx] == "theme":
                    theme_id = apply_theme(THEMES[(theme_index(theme_id) + (1 if a == "right" else -1)) % len(THEMES)][0])
                    state["theme"] = theme_id
                    save_state(state)
                elif a in ("left", "right") and SETTINGS_ROWS[set_idx] == "launcher":
                    launcher_id = LAUNCHERS[(launcher_index(launcher_id) + (1 if a == "right" else -1)) % len(LAUNCHERS)][0]
                    state["launcher"] = launcher_id
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
                    if row == "theme":         # A opens a dropdown listing every theme
                        dd = {"kind": "theme", "sel": theme_index(theme_id), "orig": theme_id}
                    elif row == "launcher":    # ... or every launcher
                        dd = {"kind": "launcher", "sel": launcher_index(launcher_id), "orig": launcher_id}
                    elif row == "splash":      # A steps on -> silent -> off
                        state["splash"] = splash.MODES[(splash.MODES.index(splash.mode_of(state)) + 1) % len(splash.MODES)]
                        save_state(state)
                    elif row == "emulators":
                        emu_idx, view = 0, "emulators"
                    elif row == "wifi":
                        wconf, wf_idx, wf_msg, wf_msg_until = wifictl.load_conf(), 0, "", 0.0
                        wscan.kick()
                        view = "wifi"
                    elif row == "bluetooth":
                        bt_idx, view = 0, "bt"
                        btm.refresh()
                    elif row == "ssh":
                        view = "ssh"
                    elif row == "info":        # read-only system information
                        info_scroll, info_pos, view = 0, 0.0, "info"
                    elif row == "swapab":      # exchange A and B for the menu AND the emulators (done in the input hub)
                        set_swap_ab(not swap_ab_on())
                        restart_hub()
                    else:                      # guided button mapping
                        bmap, view = ButtonMap(), "bmap"
            elif view == "emulators":
                emu_systems = [sy for sy in SYSTEMS if sy[2] == "retroarch" and compatible_cores(sy[0])]
                if a in ("up", "down"):
                    emu_idx = (emu_idx + (1 if a == "down" else -1)) % max(1, len(emu_systems))
                elif a in ("left", "right") and emu_systems:
                    sid = emu_systems[emu_idx][0]
                    cores = compatible_cores(sid)
                    if cores:
                        current = state.get("emulators", {}).get(sid, default_core(sid))
                        if current not in cores:
                            current = default_core(sid) if default_core(sid) in cores else cores[0]
                        pos = cores.index(current)
                        state.setdefault("emulators", {})[sid] = cores[(pos + (1 if a == "right" else -1)) % len(cores)]
                        save_state(state)
                elif a == "b":
                    view = back_to("settings")
            elif view == "info":
                if a == "b":
                    view = back_to("settings")
                elif a == "up":
                    info_scroll = max(0, info_scroll - 1)
                elif a == "down":
                    info_scroll += 1                                   # clamped when drawn
                elif a in ("left", "l"):
                    info_scroll = max(0, info_scroll - 5)
                elif a in ("right", "r"):
                    info_scroll += 5
            elif view == "kbd":
                if a in ("up", "down", "left", "right"):
                    kb.move({"up": -1, "down": 1}.get(a, 0), {"left": -1, "right": 1}.get(a, 0))
                elif a == "b":
                    kb.text = kb.text[:-1]
                elif a == "x" and kb.secret:
                    kb.show = not kb.show
                elif a in ("a", "start"):
                    res = kb.press() if a == "a" else "ok"
                    if res and kb.field.startswith("fm_"):
                        if res == "cancel" or (kb.field == "fm_rename" and fm.rename(kb.text)) or (kb.field == "fm_mkdir" and fm.mkdir(kb.text)):
                            view = "files"
                        else:
                            view, kb = "files", None       # invalid / duplicate name: fm.msg says why
                    elif res and kb.field.startswith("wifi"):
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
                    view = back_to("settings")
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
            elif view == "bt":
                brows = list(btm.rows)                    # snapshot: the worker thread replaces the list
                bn = len(brows) + 1                       # toggle, devices...
                bt_idx = min(bt_idx, bn - 1)
                if a == "down":
                    bt_idx = (bt_idx + 1) % bn
                elif a == "up":
                    bt_idx = (bt_idx - 1) % bn
                elif a == "b":
                    view = back_to("settings")
                elif a == "x":
                    btm.scan_now()
                elif bt_idx == 0 and a == "a":
                    btm.toggle()
                elif bt_idx > 0 and a in ("a", "y"):
                    d = brows[bt_idx - 1]
                    if a == "y":
                        if d["paired"]:
                            btm.act(btctl.forget, d["mac"], "Forgetting " + d["name"])
                    elif d["connected"]:
                        btm.act(btctl.disconnect, d["mac"], "Disconnecting " + d["name"])
                    elif d["paired"]:
                        btm.act(btctl.connect, d["mac"], "Connecting to " + d["name"])
                    else:
                        btm.act(btctl.pair, d["mac"], "Pairing with " + d["name"])
            elif view == "ssh":
                if a == "b":
                    view = back_to("settings")
                elif a == "a":
                    ssh_toggle()
            elif view == "bmap":
                if bmap.state == "fail" and a == "a" and bmap.can_resume():
                    bmap.resume()                       # A: ask the failed step again (everything mapped so far is kept)
                elif bmap.state in ("done", "fail") and a in ("a", "b"):
                    view = back_to("settings")          # (while mapping, buttons only feed the wizard; it times out by itself)
        if view == "bmap" and bmap:
            bmap.update()
        if forced:
            wiz, now_f = bmap, time.time()
            if wiz.state == "done":
                forced_t = forced_t or now_f
                if now_f - forced_t >= 1.5:    # leave the "Saved" message up for a moment
                    forced_next()
            elif wiz.state == "fail":
                if forced_t is None:
                    forced_t, forced_fails = now_f, forced_fails + 1
                elif now_f - forced_t >= 3.0:  # retry the failed step automatically (buttons mapped so far are kept)
                    forced_t = None
                    if bmap.can_resume():
                        bmap.resume()
                    else:
                        bmap = ButtonMap()
        if {"start", "select"} <= inp.held:
            code = EXIT_POWER

        sid, stag, score, _ext, sname = vsys[sys_idx] if vsys else NO_SYSTEM
        if view == "recents":
            games, key = recent_games(), "\0recents"
        elif view == "favorites":
            games, key = favorite_games(), "\0favorites"
        else:
            games, key = games_of(sid), sid
        idx = min(idx_by.get(key, 0), max(0, len(games) - 1))
        _now = int(time.time())
        if _now != clk_at:
            clk_at, clk = _now, time.strftime("%H:%M")

        anim_moving = anim_tick()
        XMB_UI[0] = launcher_id == "xmb"
        if launcher_id == "xmb":                               # XMB look: wave background on every screen, no title bar
            xmb_bg(canvas, time.time())
        else:
            canvas.fill(BG)
            pygame.draw.rect(canvas, PANEL, (0, 0, W, 64))
            pygame.draw.rect(canvas, ACCENT, (0, 62, W, 2))
            rg = f_logo.render("RG", True, ACCENT)             # place OS after the measured width of RG so they never overlap
            canvas.blit(rg, (20, 14))
            canvas.blit(f_logo.render("OS", True, TEXT), (20 + rg.get_width() + 4, 14))
        if view == "games":
            info = "%s   %d %s%s   %s" % (sname, len(games), "port" if sid == "ports" else "game", "" if len(games) == 1 else "s", clk)
        elif view == "systems":
            info = "Games   %s" % clk
        elif view == "recents":
            info = "Recents   %d game%s   %s" % (len(games), "" if len(games) == 1 else "s", clk)
        elif view == "favorites":
            info = "Favorites   %d game%s   %s" % (len(games), "" if len(games) == 1 else "s", clk)
        elif view == "tools":
            info = "Tools   %s" % clk
        elif view == "files" or (view == "kbd" and kb.field.startswith("fm_")):
            info = "Tools > Files   %s" % clk
        elif view in ("settings", "bmap"):
            info = "Settings   %s" % clk
        elif view == "info":
            info = "Settings > Information   %s" % clk
        elif view == "emulators":
            info = "Settings > Emulators   %s" % clk
        elif view == "wifi" or (view == "kbd" and kb.field.startswith("wifi")):
            info = "Settings > Wi-Fi   %s" % clk
        elif view == "bt":
            info = "Settings > Bluetooth   %s" % clk
        elif view == "ssh":
            info = "Settings > SSH / file transfer   %s" % clk
        else:
            info = clk
        if forced:
            info = "First-time setup   %s" % clk
        s = f_small.render(info, True, DIM)
        bat = rd_bat()
        right = W - 20
        if launcher_id == "xmb":                   # translucent status pill, as on the PS3 XMB
            pl = (W - 90 - 16 if bat else W - 20) - s.get_width() - 20
            pygame.draw.rect(canvas, xmb_tint(W - 40, 33, 0.10), (pl, 14, W - 8 - pl, 38), border_radius=19)
            pygame.draw.rect(canvas, xmb_tint(W - 40, 33, 0.24), (pl, 14, W - 8 - pl, 38), 1, border_radius=19)
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

        if view == "home" and launcher_id == "xmb":
            xcat = XMB_CATS[xmb_cat][0]
            xitems = xmb_items(xcat)
            xmb_sel[xcat] = max(0, min(xmb_sel.get(xcat, 0), len(xitems) - 1))
            xk = 1.0 - math.exp(-_anim_clock[1] * 14.0)                     # ease the bar and the list toward their targets
            xmb_cx += (xmb_cat - xmb_cx) * xk
            xmb_vy += (xmb_sel[xcat] - xmb_vy) * xk
            xmb_fade = min(1.0, xmb_fade + _anim_clock[1] * 6.0)
            if abs(xmb_cat - xmb_cx) > 0.01 or abs(xmb_sel[xcat] - xmb_vy) > 0.01 or xmb_fade < 1.0:
                _anim_clock[2] = True                                       # keep the frame rate up while it moves
            draw_xmb_home(canvas, f_row, f_small, xmb_cx, xmb_cat, xitems, xmb_vy, xmb_fade)
            hint = "Left/Right Category   Up/Down Item   A Open   X Rescan"
            if dd is not None:                                              # Theme / Launcher dropdown over the XMB
                draw_xmb_dropdown(canvas, f_small, dd, dd_options(dd["kind"]))
                hint = "Up/Down Browse   A Select   B Cancel"
        elif view == "home":
            gap = 14
            nrows_h = (len(HOME_ITEMS) + HOME_COLS - 1) // HOME_COLS
            bw, bh = (W - 40 - (HOME_COLS - 1) * gap) // HOME_COLS, (H - top_y - 48 - (nrows_h - 1) * gap) // nrows_h
            x0 = (W - (HOME_COLS * bw + (HOME_COLS - 1) * gap)) // 2
            vo = (bh - 148) // 2                                        # tile content is laid out for 148 px; centre it in the taller tile
            subs = {"games": "%d games" % len(all_games),
                    "recents": "%d recent" % len(recent_games()),
                    "favorites": "%d favorites" % len(favorite_games()),
                    "tools": "Files, ports, RetroArch",
                    "settings": "Theme, Wi-Fi, info, controls",
                    "power": "Shut down RG OS"}
            for i, (hid, hname) in enumerate(HOME_ITEMS):
                bx, by, sel = x0 + (i % HOME_COLS) * (bw + gap), top_y + (i // HOME_COLS) * (bh + gap), i == home_idx
                hl, lift = draw_button(canvas, (bx, by, bw, bh), SEL_BG if sel else PANEL, 18, 6, sel)
                by -= lift                                              # contents ride up with the face
                if bh < 130:
                    # Short tiles (RG34XXSP, 720x480: 106 px high): the stacked layout below needs ~148 px, so icon, title and
                    # subtitle ran into each other and out of the tile. Lay them out side by side instead: icon in a fixed column
                    # on the left, title and a (wrapped, at most two lines) subtitle to its right, the block centred vertically.
                    icx, tx = bx + 62, bx + 128
                    ICONS[hid](canvas, icx, by + bh // 2, _mix(DIM, ACCENT, hl), _mix(PANEL, SEL_BG, hl))
                    title = f_row.render(fit(f_row, hname, bw - (tx - bx) - 14), True, _mix(DIM, TEXT, hl))
                    sub_lines = wrap(f_small, subs[hid], bw - (tx - bx) - 14)[:2]
                    th_, lh_ = title.get_height(), f_small.get_linesize() + 1
                    ty0 = by + (bh - (th_ + 4 + lh_ * len(sub_lines))) // 2
                    canvas.blit(title, (tx, ty0))
                    for li, ln in enumerate(sub_lines):
                        canvas.blit(f_small.render(ln, True, DIM), (tx, ty0 + th_ + 4 + li * lh_))
                    continue
                cx = bx + bw // 2                                       # icon on top, title and subtitle centred below it
                ICONS[hid](canvas, cx, by + 44 + vo, _mix(DIM, ACCENT, hl), _mix(PANEL, SEL_BG, hl))
                title = f_row.render(hname, True, _mix(DIM, TEXT, hl))
                canvas.blit(title, (cx - title.get_width() // 2, by + 84 + vo))
                sub = f_small.render(fit(f_small, subs[hid], bw - 24), True, DIM)
                canvas.blit(sub, (cx - sub.get_width() // 2, by + 114 + vo))
            hint = "A Open   X Rescan   START+SELECT Power off"
            if dd is not None:                                              # Launcher preview from the XMB lands here: keep the dropdown visible
                draw_xmb_dropdown(canvas, f_small, dd, dd_options(dd["kind"]))
                hint = "Up/Down Browse   A Select   B Cancel"
        elif view == "tools":
            for i, (_tid, tname, tdesc, _tgt) in enumerate(TOOLS):
                by, sel = top_y + i * 90, i == tool_idx
                draw_button(canvas, (list_x, by, W - 2 * list_x, 82), SEL_BG if sel else PANEL, 12, 3, sel)
                canvas.blit(f_row.render(tname, True, TEXT if sel else DIM), (list_x + 24, by + 16))
                canvas.blit(f_small.render(fit(f_small, tdesc, W - 2 * list_x - 48), True, DIM), (list_x + 24, by + 46))
            hint = "A Open   B Back   START+SELECT Power off"
        elif view == "files":
            canvas.blit(f_small.render(tail_fit(f_small, fm.title(), W - 2 * list_x), True, ACCENT), (list_x, 68))
            n_fm = len(fm.entries)
            first_fm = min(max(0, fm.idx - FM_ROWS // 2), max(0, n_fm - FM_ROWS))
            if not n_fm:
                canvas.blit(f_row.render("Empty folder   (X for options)", True, DIM), (list_x + 10, 110))
            fw_ = W - 2 * list_x - 14
            for slot, e in enumerate(fm.entries[first_fm:first_fm + FM_ROWS]):
                i = first_fm + slot
                by, sel = 92 + slot * 44, i == fm.idx
                draw_button(canvas, (list_x, by, fw_, 40), SEL_BG if sel else PANEL, 10, 3, sel)
                ck = (list_x + 12, by + 12, 16, 16)                       # mark box (Y)
                pygame.draw.rect(canvas, ACCENT if sel else DIM, ck, 2, border_radius=3)
                if e["path"] in fm.marked:
                    pygame.draw.rect(canvas, ACCENT, (ck[0] + 4, ck[1] + 4, 8, 8), border_radius=2)
                ic = ACCENT if e["dir"] else DIM                           # folder / file icon
                if e["dir"]:
                    pygame.draw.rect(canvas, ic, (list_x + 40, by + 11, 10, 5), border_radius=2)
                    pygame.draw.rect(canvas, ic, (list_x + 40, by + 15, 22, 14), border_radius=3)
                else:
                    pygame.draw.rect(canvas, ic, (list_x + 44, by + 11, 15, 18), 2, border_radius=2)
                size_s = "" if e["dir"] else filemanager.human_size(e["size"])
                sz = f_small.render(size_s, True, DIM)
                canvas.blit(sz, (list_x + fw_ - 16 - sz.get_width(), by + 12))
                canvas.blit(f_row.render(fit(f_row, e["name"], fw_ - 80 - 16 - sz.get_width() - 16), True, TEXT if sel else DIM),
                            (list_x + 72, by + 7))
            if n_fm > FM_ROWS:
                bar_h = FM_ROWS * 44 - 4
                th = max(24, int(bar_h * FM_ROWS / n_fm))
                ty = 92 + int((bar_h - th) * first_fm / max(1, n_fm - FM_ROWS))
                ui_rect(canvas, PANEL, (W - list_x - 6, 92, 6, bar_h), 3)
                pygame.draw.rect(canvas, ACCENT, (W - list_x - 6, ty, 6, th), border_radius=3)
            status = fm.busy or fm.msg or ("%d marked" % len(fm.marked) if fm.marked else
                                           ("Clipboard: %d item%s to %s" % (len(fm.clip), "" if len(fm.clip) == 1 else "s", "move" if fm.clip_cut else "copy")) if fm.clip else "")
            canvas.blit(f_small.render(fit(f_small, status, W - 2 * list_x), True, ACCENT), (list_x, 420 + DY))
            if fm.busy:
                bx, by, bw, bh = W // 2 - 150, H // 2 - 30, 300, 60
                pygame.draw.rect(canvas, PANEL, (bx, by, bw, bh), border_radius=12)
                pygame.draw.rect(canvas, ACCENT, (bx, by, bw, bh), 2, border_radius=12)
                t = f_row.render(fm.busy, True, TEXT)
                canvas.blit(t, (bx + (bw - t.get_width()) // 2, by + 18))
            elif fm_pop:
                if fm_pop["kind"] == "menu":
                    its = fm_pop["items"]
                    bw, bh = 280, 24 + 40 * len(its)
                    bx, by = (W - bw) // 2, max(70, (H - bh) // 2)
                    pygame.draw.rect(canvas, PANEL, (bx, by, bw, bh), border_radius=12)
                    pygame.draw.rect(canvas, ACCENT, (bx, by, bw, bh), 2, border_radius=12)
                    for k, (_id, label) in enumerate(its):
                        ry, rs = by + 12 + k * 40, k == fm_pop["sel"]
                        if rs:
                            pygame.draw.rect(canvas, SEL_BG, (bx + 8, ry, bw - 16, 36), border_radius=8)
                        canvas.blit(f_row.render(label, True, TEXT if rs else DIM), (bx + 24, ry + 6))
                elif fm_pop["kind"] == "confirm":
                    bw, bh = 340, 130
                    bx, by = (W - bw) // 2, (H - bh) // 2
                    pygame.draw.rect(canvas, PANEL, (bx, by, bw, bh), border_radius=12)
                    pygame.draw.rect(canvas, ACCENT, (bx, by, bw, bh), 2, border_radius=12)
                    q = "Delete %d item%s?" % (fm_pop["n"], "" if fm_pop["n"] == 1 else "s")
                    t = f_row.render(q, True, TEXT)
                    canvas.blit(t, (bx + (bw - t.get_width()) // 2, by + 18))
                    for k, label in enumerate(("Yes", "No")):
                        rx, rs = bx + 30 + k * 150, k == fm_pop["sel"]
                        draw_button(canvas, (rx, by + 70, 130, 38), SEL_BG if rs else BG, 10, 3, rs)
                        t = f_row.render(label, True, TEXT if rs else DIM)
                        canvas.blit(t, (rx + (130 - t.get_width()) // 2, by + 77))
                else:
                    lines = fm_pop["lines"]
                    bw, bh = 400, 28 + 26 * len(lines)
                    bx, by = (W - bw) // 2, (H - bh) // 2
                    pygame.draw.rect(canvas, PANEL, (bx, by, bw, bh), border_radius=12)
                    pygame.draw.rect(canvas, ACCENT, (bx, by, bw, bh), 2, border_radius=12)
                    for k, ln in enumerate(lines):
                        canvas.blit((f_row if k == 0 else f_small).render(fit(f_row if k == 0 else f_small, ln, bw - 40), True, TEXT if k == 0 else DIM),
                                    (bx + 20, by + 14 + k * 26))
            hint = ("A Select   B Cancel" if fm_pop else "A Open   B Up   Y Mark   X Options   L/R Page")
        elif view == "settings":
            srows = settings_text()
            set_vis = max(1, PH // 86)                     # settings rows that fit above the hint line
            set_first = min(max(0, set_idx - set_vis // 2), max(0, len(SETTINGS_ROWS) - set_vis))
            for i, (nm, sub) in enumerate(srows[r] for r in SETTINGS_ROWS):
                if not set_first <= i < set_first + set_vis:
                    continue                               # scrolled out of view
                by, sel = top_y + (i - set_first) * 86, i == set_idx
                draw_button(canvas, (list_x, by, W - 2 * list_x, 78), SEL_BG if sel else PANEL, 12, 3, sel)
                canvas.blit(f_row.render(nm, True, TEXT if sel else DIM), (list_x + 24, by + 14))
                sub_w = W - 2 * list_x - 48
                if SETTINGS_ROWS[i] == "theme":      # colour swatches of the active theme at the right end of the row
                    sw = [THEMES[theme_index(theme_id)][2][k] for k in ("BG", "PANEL", "SEL_BG", "ACCENT", "TEXT")]
                    sx = W - list_x - 24 - len(sw) * 24
                    for j, col in enumerate(sw):
                        pygame.draw.rect(canvas, col, (sx + j * 24, by + 29, 20, 20), border_radius=5)
                        pygame.draw.rect(canvas, DIM, (sx + j * 24, by + 29, 20, 20), 1, border_radius=5)
                    sub_w = sx - (list_x + 24) - 12
                canvas.blit(f_small.render(fit(f_small, sub, sub_w), True, DIM), (list_x + 24, by + 46))
            if len(SETTINGS_ROWS) > set_vis:                # scrollbar on the right edge
                tr_h = set_vis * 86 - 8
                th_h = max(24, tr_h * set_vis // len(SETTINGS_ROWS))
                th_y = top_y + (tr_h - th_h) * set_first // max(1, len(SETTINGS_ROWS) - set_vis)
                ui_rect(canvas, PANEL, (W - 12, top_y, 6, tr_h), 3)
                pygame.draw.rect(canvas, ACCENT, (W - 12, th_y, 6, th_h), border_radius=3)
            if dd is not None:                              # dropdown list (themes / launchers), anchored under its Settings row
                opts = dd_options(dd["kind"])
                dd_rh = 36
                dd_vis = max(1, min(len(opts), (H - 50 - top_y - 8) // dd_rh))
                row_by = top_y + (SETTINGS_ROWS.index(dd["kind"]) - set_first) * 86
                dd_w, dd_h = W - 2 * list_x - 48, dd_vis * dd_rh + 12
                dd_x, dd_y = list_x + 24, min(row_by + 80, H - 50 - dd_h)
                dd_first = min(max(0, dd["sel"] - dd_vis // 2), len(opts) - dd_vis)
                pygame.draw.rect(canvas, PANEL, (dd_x, dd_y, dd_w, dd_h), border_radius=10)
                pygame.draw.rect(canvas, ACCENT, (dd_x, dd_y, dd_w, dd_h), 2, border_radius=10)
                for slot, ti in enumerate(range(dd_first, dd_first + dd_vis)):
                    ry, rsel = dd_y + 6 + slot * dd_rh, ti == dd["sel"]
                    if rsel:
                        pygame.draw.rect(canvas, SEL_BG, (dd_x + 6, ry, dd_w - 12, dd_rh - 2), border_radius=8)
                    oid, oname, oswatch, odesc = opts[ti]
                    canvas.blit(f_small.render(fit(f_small, oname + ("   (current)" if oid == dd["orig"] else ""), dd_w - 180), True, TEXT if rsel else DIM),
                                (dd_x + 18, ry + 8))
                    if oswatch:
                        for j, col in enumerate(oswatch):
                            sx = dd_x + dd_w - 18 - (len(oswatch) - j) * 24
                            pygame.draw.rect(canvas, col, (sx, ry + 6, 20, 20), border_radius=5)
                            pygame.draw.rect(canvas, DIM, (sx, ry + 6, 20, 20), 1, border_radius=5)
                    elif odesc:
                        dt_ = f_small.render(fit(f_small, odesc, dd_w - 200), True, DIM)
                        canvas.blit(dt_, (dd_x + dd_w - 18 - dt_.get_width(), ry + 8))
                if len(opts) > dd_vis:                      # little more-above / more-below markers
                    if dd_first > 0:
                        pygame.draw.polygon(canvas, ACCENT, [(dd_x + dd_w - 8, dd_y + 12), (dd_x + dd_w - 14, dd_y + 18), (dd_x + dd_w - 2, dd_y + 18)])
                    if dd_first + dd_vis < len(opts):
                        pygame.draw.polygon(canvas, ACCENT, [(dd_x + dd_w - 8, dd_y + dd_h - 12), (dd_x + dd_w - 14, dd_y + dd_h - 18), (dd_x + dd_w - 2, dd_y + dd_h - 18)])
            hint = ("Up/Down Browse   A Select   B Cancel   " if dd is not None else
                    "A Pick theme   " if SETTINGS_ROWS[set_idx] == "theme" else
                    "A Pick launcher   " if SETTINGS_ROWS[set_idx] == "launcher" else
                    "A Change   X Preview   " if SETTINGS_ROWS[set_idx] == "splash" else
                    "A Toggle   " if SETTINGS_ROWS[set_idx] == "swapab" else "A Open   ") + ("" if dd is not None else "B Back   START+SELECT Power off")
        elif view == "emulators":
            emu_systems = [sy for sy in SYSTEMS if sy[2] == "retroarch" and compatible_cores(sy[0])]
            ui_rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 336 + DY), 10)
            emu_vis = 7 + DY // 44
            first_emu = min(max(0, emu_idx - emu_vis // 2), max(0, len(emu_systems) - emu_vis))
            for slot, sy in enumerate(emu_systems[first_emu:first_emu + emu_vis]):
                i = first_emu + slot
                sid, _tag, _runner, _ext, fullname = sy
                cores = compatible_cores(sid)
                chosen = state.get("emulators", {}).get(sid, default_core(sid))
                if chosen not in cores:
                    chosen = default_core(sid) if default_core(sid) in cores else (cores[0] if cores else "No compatible core")
                y = top_y + 8 + slot * 44
                sel = i == emu_idx
                if sel:
                    ui_rect(canvas, SEL_BG, (list_x + 8, y, W - 2 * list_x - 16, 40), 8)
                    pygame.draw.rect(canvas, ACCENT, (list_x + 8, y, W - 2 * list_x - 16, 40), 2, border_radius=8)
                canvas.blit(f_small.render(fit(f_small, fullname, 300), True, TEXT if sel else DIM), (list_x + 20, y + 3))
                core_txt = chosen.replace("_", " ")
                canvas.blit(f_small.render(fit(f_small, "< " + core_txt + " >", 190), True, ACCENT if sel else TEXT), (W - list_x - 205, y + 14))
            if not any(compatible_cores(sy[0]) for sy in emu_systems):
                canvas.blit(f_row.render("No installed compatible RetroArch cores found", True, TEXT), (list_x + 24, top_y + 20))
            hint = "Up/Down System   Left/Right Change compatible core   B Back"
        elif view == "info":
            ROW_H = 42
            px, py, pw, ph = list_x, top_y, W - 2 * list_x, 336 + DY
            ui_rect(canvas, PANEL, (px, py, pw, ph), 10)
            info_rows = settings_info()
            vis = max(1, (ph - 20) // ROW_H)
            max_scroll = max(0, len(info_rows) - vis)
            info_scroll = min(max(0, info_scroll), max_scroll)
            target = info_scroll * ROW_H
            dt_ = _anim_clock[1]
            if abs(target - info_pos) < 0.5:
                info_pos = float(target)
            else:
                info_pos += (target - info_pos) * min(1.0, dt_ * 18.0)    # ease toward the target row
                _anim_clock[2] = True                                       # keep animating until it arrives
            canvas.set_clip(pygame.Rect(px, py + 4, pw, ph - 8))
            for i, (k, v) in enumerate(info_rows):
                y = int(py + 14 + i * ROW_H - info_pos)
                if y < py - ROW_H or y > py + ph:
                    continue
                canvas.blit(f_row.render(k, True, DIM), (px + 24, y))
                canvas.blit(f_row.render(fit(f_row, v, pw - 48 - 190 - 14), True, TEXT), (px + 214, y))
            canvas.set_clip(None)
            if max_scroll > 0:                                              # scrollbar
                track_h = ph - 24
                bar_h = max(24, int(track_h * vis / len(info_rows)))
                bar_y = py + 12 + int((track_h - bar_h) * (info_pos / (max_scroll * ROW_H)))
                pygame.draw.rect(canvas, DIM, (px + pw - 10, py + 12, 4, track_h), 1, border_radius=2)
                pygame.draw.rect(canvas, ACCENT, (px + pw - 10, bar_y, 4, bar_h), 0, border_radius=2)
                if info_scroll < max_scroll:
                    canvas.blit(f_tiny.render("v more", True, DIM), (px + pw - 70, py + ph - 18))
                if info_scroll > 0:
                    canvas.blit(f_tiny.render("^ more", True, DIM), (px + pw - 70, py + 6))
            hint = "Up/Down Scroll   L/R Page   B Back   START+SELECT Power off"
        elif view == "wifi":
            wst, wrows = wifictl.status(), wscan.rows(wconf)
            wn = len(wrows) + 2
            ui_rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 336 + DY), 10)
            head = wifictl.status_text(wst) if wconf["enabled"] else "Wi-Fi is off"
            canvas.blit(f_row.render(fit(f_row, head, W - 2 * list_x - 56), True, ACCENT), (list_x + 28, top_y + 8))
            if wst["state"] == "connected" and wst["rssi"] is not None:
                canvas.blit(f_small.render("%d dBm" % wst["rssi"], True, DIM), (W - list_x - 90, top_y + 12))
            vis = 7 + DY // 40
            first = min(max(0, wf_idx - vis // 2), max(0, wn - vis))
            for slot in range(min(vis, wn)):
                i = first + slot
                y, sel = top_y + 44 + slot * 40, i == wf_idx
                if sel:
                    ui_rect(canvas, SEL_BG, (list_x + 6, y, W - 2 * list_x - 12, 38), 8)
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
                canvas.blit(f_small.render(fit(f_small, note, W - 2 * list_x - 56), True, TEXT), (list_x + 28, top_y + 310 + DY))
            hint = "A Connect   X Scan   Y Forget   B Back"
        elif view == "bt":
            brows = list(btm.rows)
            bn = len(brows) + 1
            bt_idx = min(bt_idx, bn - 1)
            ui_rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 336 + DY), 10)
            head = ("No Bluetooth adapter found (see rgos-logs.txt)" if btm.on is None and not btctl.adapter() else
                    "Starting Bluetooth..." if btm.on is None else "Bluetooth is on" if btm.on else "Bluetooth is off")
            canvas.blit(f_row.render(fit(f_row, head, W - 2 * list_x - 56), True, ACCENT), (list_x + 28, top_y + 8))
            vis = 7 + DY // 40
            first = min(max(0, bt_idx - vis // 2), max(0, bn - vis))
            for slot in range(min(vis, bn)):
                i = first + slot
                y, sel = top_y + 44 + slot * 40, i == bt_idx
                if sel:
                    ui_rect(canvas, SEL_BG, (list_x + 6, y, W - 2 * list_x - 12, 38), 8)
                    pygame.draw.rect(canvas, ACCENT, (list_x + 6, y, 5, 38), border_radius=3)
                col = TEXT if sel else DIM
                if i == 0:
                    on = bool(btm.on)
                    pygame.draw.rect(canvas, ACCENT if on else DIM, (list_x + 28, y + 11, 16, 16), 0 if on else 2, border_radius=3)
                    canvas.blit(f_row.render("Bluetooth", True, col), (list_x + 56, y + 8))
                    t = f_small.render("On" if on else "Off", True, DIM)
                    canvas.blit(t, (W - list_x - 28 - t.get_width(), y + 12))
                else:
                    d = brows[i - 1]
                    canvas.blit(f_row.render(fit(f_row, d["name"], W - 2 * list_x - 230), True, col), (list_x + 56, y + 8))
                    tag = "connected" if d["connected"] else "paired" if d["paired"] else ""
                    t = f_small.render(tag, True, ACCENT if d["connected"] else DIM)
                    canvas.blit(t, (W - list_x - 28 - t.get_width(), y + 12))
            if time.time() < btm.msg_until:
                note = btm.msg
            elif btm.scanning:
                note = "Scanning... put your device in pairing mode"
            elif btm.on and not brows:
                note = "No devices - put the device in pairing mode, then press X to scan"
            else:
                note = ""
            if note:
                canvas.blit(f_small.render(fit(f_small, note, W - 2 * list_x - 56), True, TEXT), (list_x + 28, top_y + 310 + DY))
            hint = "A Pair/Connect/Disconnect   X Scan   Y Forget   B Back"
        elif view == "ssh":
            si = ssh_info()
            wst_ = wifictl.status()
            ip_ = wst_["ip"] or "(connect Wi-Fi first)"
            ui_rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 336 + DY), 10)
            canvas.blit(f_row.render("SSH / file transfer is " + ("ON" if si["running"] else "off"), True, ACCENT), (list_x + 28, top_y + 14))
            lines_ = [("Address", ip_), ("Port", str(si["port"])), ("User", si["user"]), ("Password", si["password"]),
                      ("Files", "sftp://root@%s   (WinSCP, FileZilla, scp)" % (wst_["ip"] or "<ip>")),
                      ("Folders", "/media/roms = ROMS partition,  /boot = boot partition")]
            for k, (kk, vv) in enumerate(lines_):
                canvas.blit(f_row.render(kk, True, DIM), (list_x + 28, top_y + 62 + k * 40))
                canvas.blit(f_row.render(fit(f_row, vv, W - 2 * list_x - 56 - 150), True, TEXT), (list_x + 178, top_y + 62 + k * 40))
            canvas.blit(f_small.render(fit(f_small, "Change the password: put ssh-password.txt on the boot partition. Use a key: authorized_keys.", W - 2 * list_x - 56), True, DIM),
                        (list_x + 28, top_y + 310 + DY))
            hint = "A Turn " + ("off" if si["running"] else "on") + "   B Back"
        elif view == "kbd":
            KW = 636 + DX
            KX = (W - KW) // 2
            canvas.blit(f_small.render(kb.title, True, DIM), (KX, top_y - 4))
            ui_rect(canvas, PANEL, (KX, top_y + 16, KW, 52), 8)
            pygame.draw.rect(canvas, ACCENT, (KX, top_y + 16, KW, 52), 2, border_radius=8)
            shown = kb.text if kb.show else "*" * len(kb.text)
            canvas.blit(f_row.render(tail_fit(f_row, shown, 590 + DX) + ("_" if int(time.time() * 2) % 2 == 0 else ""), True, TEXT),
                        (KX + 14, top_y + 31))
            for r in range(5):
                keys = kb.keys(r)
                ky = top_y + 92 + r * 80 + (8 if r == 4 else 0)
                kw = (KW - 9 * 4) // 10 if r < 4 else (KW - 4 * 4) // 5
                for c, k in enumerate(keys):
                    kx = KX + c * (kw + 4)
                    sel = (r == kb.row and c == kb.col)
                    draw_button(canvas, (kx, ky, kw, 70), SEL_BG if sel else PANEL, 8, 4, sel)
                    label = KB_LAYER_NAMES[(kb.layer + 1) % 3] if k == "Layer" else k
                    t = f_row.render(label, True, ACCENT if (sel and r == 4) else (TEXT if sel else DIM))
                    canvas.blit(t, (kx + (kw - t.get_width()) // 2, ky + (70 - t.get_height()) // 2))
            hint = "A Type   B Delete   START Done" + ("   X Show/hide" if kb.secret else "")
        elif view == "bmap":
            ui_rect(canvas, PANEL, (list_x, top_y, W - 2 * list_x, 330 + DY), 14)
            sview = bmap.stick_view()
            canvas.blit(f_big.render("Stick calibration" if sview else "Button mapping", True, ACCENT), (list_x + 28, top_y + 22))
            if sview:
                canvas.blit(f_small.render("Stick %d of %d" % (min(bmap.cstick + 1, len(bmap.sticks)) if bmap.state == "sroll" else 1, len(bmap.sticks)),
                                           True, DIM), (list_x + 28, top_y + 58))
            elif bmap.state in ("prompt", "apply"):
                n = min(bmap.step + 1, bmap.total())
                canvas.blit(f_small.render("Button %d of %d" % (n, bmap.total()), True, DIM), (list_x + 28, top_y + 58))
            text_w = W - 2 * list_x - 60 - (170 if sview else 0)
            for i, line in enumerate(bmap.lines()):
                for j, wl in enumerate(wrap(f_big if i == 0 else f_row, line, text_w)):
                    canvas.blit((f_big if i == 0 else f_row).render(wl, True, TEXT if i == 0 else DIM),
                                (list_x + 28, top_y + 110 + i * 56 + j * 26))
            if sview:                                           # live picture of the stick: ring, four edge marks that light up, and a dot
                _, sx, sy, sreach = sview
                gcx, gcy, gr = W - list_x - 28 - 70, top_y + 165 + DY // 2, 62
                pygame.draw.circle(canvas, BG, (gcx, gcy), gr)
                pygame.draw.circle(canvas, DIM, (gcx, gcy), gr, 2)
                pygame.draw.line(canvas, DIM, (gcx - gr, gcy), (gcx + gr, gcy), 1)
                pygame.draw.line(canvas, DIM, (gcx, gcy - gr), (gcx, gcy + gr), 1)
                for (ai, sg) in ((0, -1), (0, 1), (1, -1), (1, 1)):
                    mx, my = gcx + (sg * gr if ai == 0 else 0), gcy + (sg * gr if ai == 1 else 0)
                    pygame.draw.circle(canvas, ACCENT if (ai, sg) in sreach else PANEL, (mx, my), 8)
                    pygame.draw.circle(canvas, DIM, (mx, my), 8, 1)
                pygame.draw.circle(canvas, TEXT, (gcx + int(sx * (gr - 10)), gcy + int(sy * (gr - 10))), 8)
            hint = (("A Retry this step   B Back" if bmap.can_resume() and not forced else "A/B Back") if bmap.state in ("done", "fail") else
                    "Move the stick as shown (a stick that is not moved is skipped)" if sview else
                    "Press the button shown (wizard stops by itself if idle)")
            if forced:
                hint = "Required first-time setup" + ("   MENU Skip" if forced_fails >= 3 else "")
        elif view == "systems":
            if not vsys:
                for i, m in enumerate(["No games found.",
                                       "Put GBA ROMs in roms/gba/, NES ROMs in roms/nes/",
                                       "Mega Drive ROMs in roms/genesis/ (MD, GEN, SMD, BIN).",
                                       "or launcher scripts in roms/ports/, then press X (B = back)."]):
                    canvas.blit((f_big if i == 0 else f_row).render(m, True, TEXT if i == 0 else DIM),
                                (40, 150 + i * 34))
            sys_rows = 6 + DY // 56                # rows that fit between the header and the hint line
            sys_first = max(0, min(sys_idx - sys_rows // 2, len(vsys) - sys_rows))
            for r, (xid, xtag, xcore, _e, xname) in enumerate(vsys):
                if not sys_first <= r < sys_first + sys_rows:
                    continue
                y, sel, cnt = top_y + (r - sys_first) * 56, r == sys_idx, len(games_of(xid))
                if sel:
                    ui_rect(canvas, SEL_BG, (list_x, y, list_w, 52), 8)
                    pygame.draw.rect(canvas, ACCENT, (list_x, y, 5, 52), border_radius=3)
                ui_rect(canvas, PANEL if not sel else BG, (list_x + 18, y + 6, 64, 40), 6)
                t = f_big.render(xtag, True, ACCENT if sel else DIM)
                canvas.blit(t, (list_x + 50 - t.get_width() // 2, y + 13))
                canvas.blit(f_row.render(fit(f_row, xname, list_w - 220), True, TEXT if sel else DIM),
                            (list_x + 98, y + 4))
                canvas.blit(f_small.render("%d %s%s" % (cnt, "port" if xid == "ports" else "game",
                                                         "" if cnt == 1 else "s"), True, DIM),
                            (list_x + 98, y + 28))
            px, cnt = 500 + DX, len(games)
            if vsys:
                ui_rect(canvas, PANEL, (px, top_y, 200, PH), 10)
                ui_rect(canvas, SEL_BG, (px + 20, top_y + 20, 160, 110 + DY // 2), 8)
                ini = f_logo.render(stag, True, ACCENT)
                canvas.blit(ini, (px + 100 - ini.get_width() // 2, top_y + 52 + DY // 4))
                for i, line in enumerate(wrap(f_row, sname, 170)):
                    canvas.blit(f_row.render(line, True, TEXT), (px + 15, top_y + 148 + DY // 2 + i * 26))
                canvas.blit(f_small.render("%d %s%s" % (cnt, "port" if sid == "ports" else "game", "" if cnt == 1 else "s"), True, DIM),
                            (px + 15, top_y + 300 + DY))
                canvas.blit(f_small.render("Native Linux games" if score == "native" else "RetroArch + " + core_label(sid, state), True, DIM), (px + 15, top_y + 322 + DY))
            if not os.path.isdir(ROMS_DIR):
                canvas.blit(f_small.render("ROMS partition not found.", True, ACCENT), (list_x + 18, H - 52))
            hint = "A Open   B Back   X Rescan   START+SELECT Power off"
        elif not games:
            if view == "recents":
                msg = ["No recent games yet.",
                       "Games you run show up here, newest first.",
                       "(B = back)"]
            elif view == "favorites":
                msg = ["No favorite games yet.",
                       "In a game's list, highlight a game and press Y.",
                       "Favorites are saved to favorites.txt."]
            else:
                msg = ["No games found.",
                       "Put GBA ROMs in roms/gba/, NES ROMs in roms/nes/",
                       "Mega Drive ROMs in roms/genesis/ (MD, GEN, SMD, BIN).",
                       "or launcher scripts in roms/ports/, then X (B = back)."]
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
                    ui_rect(canvas, SEL_BG, (list_x, y, list_w, row_h - 4), 8)
                    pygame.draw.rect(canvas, ACCENT, (list_x, y, 5, row_h - 4), border_radius=3)
                badges = []
                if games[gi]["path"] in favorite_paths:
                    badges.append("★")
                if games[gi].get("card", 1) == 2:
                    badges.append("SD2")
                suffix = "  ".join(badges)
                canvas.blit(f_row.render(fit(f_row, games[gi]["title"], list_w - 40 - (len(suffix) * 10 if suffix else 0)),
                                         True, TEXT if gi == idx else DIM), (list_x + 18, y + 9))
                if suffix:
                    bt = f_tiny.render(suffix, True, ACCENT)
                    canvas.blit(bt, (list_x + list_w - 14 - bt.get_width(), y + 14))
            if len(games) > rows:
                bar_h = H - 64 - top_y
                th = max(24, int(bar_h * rows / len(games)))
                ty = top_y + int((bar_h - th) * first / max(1, len(games) - rows))
                ui_rect(canvas, PANEL, (list_x + list_w + 6, top_y, 6, bar_h), 3)
                pygame.draw.rect(canvas, ACCENT, (list_x + list_w + 6, ty, 6, th), border_radius=3)

            g = games[idx]
            px = 500 + DX
            ui_rect(canvas, PANEL, (px, top_y, 200, PH), 10)
            ui_rect(canvas, SEL_BG, (px + 20, top_y + 14, 160, 130 + DY // 2), 8)
            ini = f_logo.render(g["title"][:1].upper(), True, ACCENT)
            canvas.blit(ini, (px + 100 - ini.get_width() // 2, top_y + 62 + DY // 4))
            ty_ = top_y + 152 + DY // 2
            for line in wrap_n(f_row, g["title"], 170, 2):
                canvas.blit(f_row.render(line, True, TEXT), (px + 15, ty_))
                ty_ += 24
            canvas.blit(f_small.render("%.1f MB" % (g["size"] / 1048576), True, DIM),
                        (px + 15, top_y + 300 + DY))
            canvas.blit(f_small.render("Native port", True, DIM), (px + 15, top_y + 322 + DY))
            hint = ("A Run   Y Favorite/unfavorite   B Back   L/R Page   X Rescan   START+SELECT Power off"
                    if view == "games" else "A Run   B Back   L/R Page   X Rescan   START+SELECT Power off")

        if inp.conf.get("debug") and inp.last_raw:
            hint += "   [" + inp.last_raw + "]"
        canvas.blit(f_small.render(hint, True, DIM), (20, H - 28))

        vol = rd_vol()                          # volume popup (VOL+/VOL- are handled by the rgos-volume service)
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

        bri = rd_bri()                          # brightness popup (SELECT+VOL, handled by the rgos-hw service)
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
        # nothing pressed for 3 s on a plain list screen and no popup showing: 15 fps is plenty (saves CPU and battery)
        idle = (view in IDLE_VIEWS and not forced and time.monotonic() - last_act > 3.0
                and time.time() >= max(vol_show_until, bri_show_until))
        clock.tick(60 if anim_moving else (15 if idle else 30))   # 60 fps only while a button animation is moving

    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass
    if not windowed:
        fbout.set_graphics(False)
    pygame.quit()
    sys.exit(code)


def run():
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        log("CRASH\n" + traceback.format_exc())
        fbout.set_graphics(False)      # give the text console back so the error is readable
        sys.exit(1)


if __name__ == "__main__":
    run()
