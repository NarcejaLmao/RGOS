"""Direct evdev gamepad reader for RG OS (no SDL joystick / udev classification needed).

Button codes come from the RG34XX-SP device tree (rocknix-singleadc-joypad node):
physical A = BTN_EAST (0x131), B = BTN_SOUTH (0x130), X = BTN_WEST (0x134), Y = BTN_NORTH (0x133)  (menu names follow the labels on the hardware: evdev's X/Y naming is Xbox-style, so swapped),
L = BTN_TL (0x136), R = BTN_TR (0x137), SELECT = 0x13a, START = 0x13b, MENU/FN = BTN_MODE (0x13c), D-pad = BTN_DPAD_* (0x220-0x223).
"""
import glob
import fcntl, glob, os, struct, time

EVFMT = struct.Struct("llHHi")          # struct input_event on 64-bit: sec, usec, type, code, value
EV_KEY, EV_ABS = 1, 3
CODES = {0x131: "a", 0x130: "b", 0x133: "y", 0x134: "x", 0x136: "l", 0x137: "r",
         0x13a: "select", 0x13b: "start", 0x13c: "menu", 0x220: "up", 0x221: "down", 0x222: "left", 0x223: "right"}
EVIOCGNAME_128 = 0x80804506             # _IOC(_IOC_READ, 'E', 0x06, 128)
EVIOCGBIT_KEY = 0x80604521              # _IOC(_IOC_READ, 'E', 0x20 + EV_KEY, 96): which key codes the device has

# Auto-mapping for devices that are not "standard" BTN_SOUTH/EAST/... pads (the TrimUI Smart Pro's kernel exposes the
# handheld's buttons in a vendor-specific way). Same tables as /opt/rgos/rgos-sticks, expressed in the menu's codes.
BTN_ALIASES = {0x120: 0x130, 0x121: 0x131, 0x122: 0x133, 0x123: 0x134, 0x124: 0x136, 0x125: 0x137,   # BTN_TRIGGER.. (joystick layout)
               0x128: 0x13a, 0x129: 0x13b, 0x12a: 0x13c}                                                # BTN_BASE3..5 = SELECT START MENU
DPAD_KEYS = {103: 0x220, 108: 0x221, 105: 0x222, 106: 0x223}                                           # KEY_UP/DOWN/LEFT/RIGHT
KBD_ALIASES = {57: 0x131, 29: 0x130, 42: 0x133, 56: 0x134, 18: 0x136, 20: 0x137,                       # keyboard-style handheld layout:
               97: 0x13a, 28: 0x13b, 1: 0x13c, 102: 0x13c, 139: 0x13c}                                  # SPACE LCTRL LSHIFT LALT E T RCTRL ENTER ESC HOME MENU
ORDERED_FILL = [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, None, None, 0x13a, 0x13b, 0x13c]            # unknown pad: buttons in code order (L2/R2/.. unused by the menu)
GAMEPAD_RANGE = range(0x100, 0x140)


def make_profile(keys):
    """{source key code: menu code} for a device whose key bitmap is `keys` (a set of codes). Pure logic."""
    prof = {c: c for c in keys if c in CODES}
    pad = sorted(c for c in keys if c in GAMEPAD_RANGE)
    known_pad = [c for c in pad if c in CODES or c in BTN_ALIASES]
    for c in keys:
        if c in BTN_ALIASES:
            prof[c] = BTN_ALIASES[c]
        elif c in DPAD_KEYS:
            prof[c] = DPAD_KEYS[c]
    if pad and not known_pad:                       # nothing recognisable among the gamepad-range buttons: assign in order
        for c, canon in zip(pad, ORDERED_FILL):
            if canon is not None:
                prof[c] = canon
    elif not (set(keys) & set(CODES)):              # no standard BTN_* pad: this is a keyboard-style handheld
        for c in keys:
            if c in KBD_ALIASES:
                prof[c] = KBD_ALIASES[c]
    return prof


class EvPads:
    def __init__(self, pattern="/dev/input/event*"):
        self.pattern, self.fds, self.names, self.next_scan = pattern, {}, {}, 0.0
        self.profiles = {}
        self.keybits = {}

    def scan(self):
        now = time.time()
        if now < self.next_scan:
            return
        self.next_scan = now + 3.0      # retry periodically: the joypad driver can appear late
        for path in glob.glob(self.pattern):
            if path in self.fds:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                buf = bytearray(128)
                fcntl.ioctl(fd, EVIOCGNAME_128, buf)
                name = bytes(buf).split(b"\0")[0].decode(errors="replace")
            except OSError:
                name = os.path.basename(path)
            self.fds[path], self.names[path] = fd, name
            try:
                buf = bytearray(96)
                fcntl.ioctl(fd, EVIOCGBIT_KEY, buf)
                keys = {i for i in range(96 * 8) if buf[i >> 3] >> (i & 7) & 1}
            except OSError:
                keys = set(CODES)
            self.profiles[path] = make_profile(keys)
            self.keybits[path] = keys

    def read(self):
        """Return a list of (type, code, value) events from all input devices."""
        self.scan()
        out = []
        for path, fd in list(self.fds.items()):
            while True:
                try:
                    data = os.read(fd, EVFMT.size * 64)
                except BlockingIOError:
                    break
                except OSError:
                    os.close(fd)
                    del self.fds[path], self.names[path]
                    self.profiles.pop(path, None)
                    self.keybits.pop(path, None)
                    break
                if not data:
                    break
                for off in range(0, len(data) - EVFMT.size + 1, EVFMT.size):
                    _, _, t, c, v = EVFMT.unpack_from(data, off)
                    if t == EV_KEY:
                        c = self.profiles.get(path, {}).get(c, c)     # auto-mapped to the menu's codes
                    out.append((t, c, v))
        return out


def diag_lines(pads, sdl_joysticks):
    """Three short status lines for the on-screen debug strip."""
    def rd(p):
        try:
            with open(p) as f:
                return f.read()
        except OSError:
            return ""
    mounted = any(l.split()[1:2] == ["/media/roms"] for l in rd("/proc/mounts").splitlines())
    exfat = "exfat" in rd("/proc/filesystems")
    build = "".join(l[9:] for l in rd("/etc/rgos-release").splitlines() if l.startswith("BUILD_ID="))
    names = [n.replace("gpio-keys-", "") for n in pads.names.values()]
    # state of the joypad platform device: bound to a driver, present but unbound, or not in the device tree
    dev = "/sys/bus/platform/devices/rocknix-singleadc-joypad"
    if os.path.exists(dev + "/driver"):
        joy = "BOUND"
    elif os.path.exists(dev):
        joy = "unbound"
    else:
        joy = "nodev"
    drivers = os.listdir("/sys/bus/platform/drivers") if os.path.isdir("/sys/bus/platform/drivers") else []
    has_drv = any("joypad" in d for d in drivers)
    iio = len(glob.glob("/sys/bus/iio/devices/iio:device*"))
    mods = len([l for l in rd("/proc/modules").splitlines() if l.strip()])
    defer = [l.strip().replace("\t", " ") for l in rd("/sys/kernel/debug/devices_deferred").splitlines()
             if "joypad" in l or "adc" in l]
    hub = rd("/run/rgos/sticks.status").strip()      # written by rgos-sticks while it runs
    return [
        "k%s joy-ev %d roms %s exfat %s b%s" % (os.uname().release.split("-")[0], len(pads.fds),
                                               "yes" if mounted else "NO", "yes" if exfat else "NO", build or "?"),
        ("in: " + " | ".join(names)) if names else "in: NO /dev/input/event* devices",
        hub or ("hub:OFF joy:%s drv:%s iio:%d mod:%d %s" % (joy, "yes" if has_drv else "NO", iio, mods,
                                                             ("defer: " + defer[0]) if defer else "")),
    ]
