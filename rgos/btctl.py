#!/usr/bin/env python3
"""RG OS Bluetooth: bring-up at boot (modules, rfkill, dbus, bluetoothd), and the helpers the menu uses
(Settings > Bluetooth): power, scan, pair, connect, disconnect, forget.

Everything is built on BlueZ (bluetoothd + bluetoothctl) and the kernel's Bluetooth modules, which come from the stock
ROCKNIX kernel. The adapter is whatever the kernel shows in /sys/class/bluetooth.

Files:  /var/lib/rgos/bt.json            on/off (kept on the FAT partition)
        /var/lib/rgos/bluetooth.tar.gz   pairings (a copy of /var/lib/bluetooth, restored at boot; FAT cannot hold the
                                         permissions bluetoothd wants, so it is stored as an archive)
        /var/log/rgos-bt.log             what happened at boot (copied to rgos-logs.txt on the card)
Pairing uses the "NoInputNoOutput" agent (Just Works), which is what game controllers, keyboards and most headsets use.
"""
import glob, json, os, re, select, subprocess, sys, tarfile, threading, time, uuid

CONF = os.environ.get("RGOS_BT_CONF", "/var/lib/rgos/bt.json")
PAIRINGS = os.environ.get("RGOS_BT_PAIRINGS", "/var/lib/rgos/bluetooth.tar.gz")
STATE_DIR = "/var/lib/bluetooth"
SYSBT = "/sys/class/bluetooth"
BLUETOOTHD = ["/usr/libexec/bluetooth/bluetoothd", "/usr/lib/bluetooth/bluetoothd", "/usr/sbin/bluetoothd"]
MODULES = ["bluetooth", "rfcomm", "bnep", "hidp", "uhid", "hid-generic", "hid-sony", "hid-playstation", "hid-nintendo",
           "btrtl", "btbcm", "btintel", "btmtk", "btqca", "hci_uart", "btusb"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02|\r")
MAC = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.I)


def log(msg):
    print("%s rgos-bt: %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(cmd, timeout=10, inp=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace", input=inp)
        return r.returncode, ANSI.sub("", r.stdout + r.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


# ---------------------------------------------------------------- on/off setting
def enabled():
    try:
        with open(CONF) as f:
            return bool(json.load(f).get("enabled", True))
    except (OSError, ValueError, AttributeError):
        return True


def set_enabled(on):
    try:
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        with open(CONF, "w") as f:
            json.dump({"enabled": bool(on)}, f)
    except OSError:
        pass
    run(["rfkill", "unblock", "bluetooth"])
    run(["bluetoothctl", "power", "on" if on else "off"])


# ---------------------------------------------------------------- pairings kept across reboots
def restore_pairings():
    if not os.path.exists(PAIRINGS):
        return
    try:
        with tarfile.open(PAIRINGS) as t:
            t.extractall("/", filter="fully_trusted") if sys.version_info >= (3, 12) else t.extractall("/")
        log("restored pairings from %s" % PAIRINGS)
    except Exception as e:
        log("could not restore pairings: %r" % (e,))


def save_pairings():
    try:
        os.makedirs(os.path.dirname(PAIRINGS), exist_ok=True)
        tmp = PAIRINGS + ".tmp"
        with tarfile.open(tmp, "w:gz") as t:
            if os.path.isdir(STATE_DIR):
                t.add(STATE_DIR, arcname=STATE_DIR.lstrip("/"))
        os.replace(tmp, PAIRINGS)
        return True
    except Exception as e:
        log("could not save pairings: %r" % (e,))
        return False


# ---------------------------------------------------------------- boot bring-up
def module_names():
    names = list(MODULES)
    for root, _d, files in os.walk("/lib/modules"):
        if "/bluetooth" in root:
            names += [re.sub(r"\.ko(\..*)?$", "", f) for f in files if ".ko" in f]
        else:
            names += [re.sub(r"\.ko(\..*)?$", "", f) for f in files if re.match(r"(btrtl|rtk_bt|.*_bt(_uart|_usb)?|bt_.*)\.ko", f)]
    return list(dict.fromkeys(names))


def adapter():
    try:
        hci = sorted(n for n in os.listdir(SYSBT) if re.fullmatch(r"hci\d+", n))
        return hci[0] if hci else None
    except OSError:
        return None


def start():
    log("start")
    for m in module_names():
        run(["modprobe", m], timeout=20)
    run(["rfkill", "unblock", "bluetooth"])
    for _ in range(30):                                            # the controller can take a while to appear
        if adapter():
            break
        time.sleep(1)
    if not adapter():
        rc, out = run(["rfkill", "list"])
        log("no Bluetooth adapter in %s after 30 s (kernel/driver does not provide one?). rfkill: %s" % (SYSBT, out.strip().replace("\n", " | ")))
        return 1
    log("adapter %s" % adapter())
    restore_pairings()
    os.makedirs("/run/dbus", exist_ok=True)
    os.makedirs("/var/lib/dbus", exist_ok=True)
    if not os.path.exists("/var/lib/dbus/machine-id"):
        with open("/var/lib/dbus/machine-id", "w") as f:
            f.write(uuid.uuid4().hex + "\n")
    if not os.path.exists("/run/dbus/system_bus_socket"):
        rc, out = run(["dbus-daemon", "--system", "--fork"])
        log("dbus-daemon rc=%s %s" % (rc, out.strip()))
        for _ in range(20):
            if os.path.exists("/run/dbus/system_bus_socket"):
                break
            time.sleep(0.25)
    exe = next((p for p in BLUETOOTHD if os.path.exists(p)), None)
    if not exe:
        log("bluetoothd not found (bluez missing from the image)")
        return 1
    rc, _ = run(["pidof", "bluetoothd"])
    if rc != 0:
        subprocess.Popen([exe, "-n"], stdout=sys.stdout, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(30):
        rc, out = run(["bluetoothctl", "show"], timeout=5)
        if rc == 0 and "Controller" in out:
            break
        time.sleep(0.5)
    else:
        log("bluetoothd did not answer: %s" % out.strip())
        return 1
    if enabled():
        run(["bluetoothctl", "power", "on"])
    run(["bluetoothctl", "pairable", "on"])
    run(["bluetoothctl", "discoverable", "off"])
    rc, out = run(["bluetoothctl", "show"])
    log("ready: %s" % " ".join(l.strip() for l in out.splitlines() if re.search(r"Controller|Powered|Name:", l)))
    return 0


# ---------------------------------------------------------------- what the menu uses
def parse_info(text):
    """`bluetoothctl info A\\ninfo B ...` output -> {mac: {name, paired, connected, trusted, rssi}}"""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"\s*Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2})", line, re.I)
        if m:
            cur = out.setdefault(m.group(1).upper(), {"name": "", "paired": False, "connected": False, "trusted": False, "rssi": None})
            continue
        if cur is None or ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        if k in ("Alias", "Name") and (k == "Alias" or not cur["name"]):
            cur["name"] = v
        elif k in ("Paired", "Connected", "Trusted"):
            cur[k.lower()] = v.lower().startswith("yes")
        elif k == "RSSI":
            m2 = re.search(r"\(?(-?\d+)\)?\s*$", v)
            cur["rssi"] = int(m2.group(1)) if m2 else None
    return out


def devices():
    """[{mac, name, paired, connected, trusted, rssi}] paired ones first, then the strongest unpaired ones; nameless ads dropped."""
    rc, out = run(["bluetoothctl", "devices"], timeout=6)
    macs = [m.group(1).upper() for m in re.finditer(r"Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2})", out, re.I)] if rc == 0 else []
    if not macs:
        return []
    rc, text = run(["bluetoothctl"], timeout=15, inp="".join("info %s\n" % m for m in macs) + "quit\n")
    info = parse_info(text)
    rows = []
    for mac in macs:
        d = info.get(mac)
        if not d:
            continue
        if not d["paired"] and (not d["name"] or d["name"].replace("-", ":").upper() == mac):
            continue                                              # anonymous BLE advertisement
        rows.append(dict(d, mac=mac))
    rows.sort(key=lambda d: (not d["paired"], not d["connected"], -(d["rssi"] if d["rssi"] is not None else -200)))
    return rows[:16]


def powered():
    rc, out = run(["bluetoothctl", "show"], timeout=5)
    if rc != 0 or "Controller" not in out:
        return None                                               # no adapter / bluetoothd not running
    return bool(re.search(r"Powered:\s*yes", out))


def scan(seconds=8):
    run(["rfkill", "unblock", "bluetooth"])
    run(["bluetoothctl", "power", "on"])
    run(["bluetoothctl", "--timeout", str(seconds), "scan", "on"], timeout=seconds + 6)


def _converse(cmds, waits, timeout):
    """Feed bluetoothctl commands on stdin one by one; after each, read output until a line matching waits[i] appears (or timeout).
    Returns the list of outputs."""
    p = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    outs = []
    try:
        for cmd, pat in zip(cmds, waits):
            p.stdin.write(cmd + "\n"); p.stdin.flush()
            buf, end = "", time.time() + (timeout if pat else 1.0)
            while time.time() < end:
                r, _, _ = select.select([p.stdout], [], [], 0.3)
                if r:
                    chunk = os.read(p.stdout.fileno(), 4096).decode("utf-8", "replace")
                    if not chunk:
                        break
                    buf += ANSI.sub("", chunk)
                    if pat and re.search(pat, buf):
                        break
            outs.append(buf)
        return outs
    finally:
        try:
            p.stdin.write("quit\n"); p.stdin.flush()
        except OSError:
            pass
        try:
            p.wait(3)
        except subprocess.TimeoutExpired:
            p.kill()


def pair(mac):
    """Pair, trust and connect. Returns (ok, message)."""
    if not MAC.match(mac):
        return False, "bad address"
    outs = _converse(["agent NoInputNoOutput", "default-agent", "pair " + mac, "trust " + mac, "connect " + mac],
                     [None, None, r"Pairing successful|Failed to pair|AlreadyExists|Already Exists|not available",
                      r"trust succeeded|not available|Failed", r"Connection successful|Failed to connect"], 35)
    paired = re.search(r"Pairing successful|AlreadyExists|Already Exists", outs[2]) is not None
    connected = "Connection successful" in outs[4]
    save_pairings()
    if connected:
        return True, "Connected"
    if paired:
        return True, "Paired (connect failed: put the device in pairing mode / switch it on)"
    return False, "Pairing failed (is the device in pairing mode?)"


def connect(mac):
    rc, out = run(["bluetoothctl", "connect", mac], timeout=25)
    return ("Connection successful" in out), ("Connected" if "Connection successful" in out else "Could not connect")


def disconnect(mac):
    run(["bluetoothctl", "disconnect", mac], timeout=10)
    return True, "Disconnected"


def forget(mac):
    run(["bluetoothctl", "remove", mac], timeout=10)
    save_pairings()
    return True, "Forgot device"


class Manager:
    """Background worker for the menu: refresh() / scan_now() / act(fn, mac) never block the UI."""
    def __init__(self):
        self.rows, self.on, self.busy, self.msg, self.msg_until, self.scanning = [], None, False, "", 0.0, False

    def _bg(self, fn):
        if self.busy:
            return
        self.busy = True

        def job():
            try:
                fn()
            except Exception as e:
                self.say("Error: %r" % (e,))
            finally:
                self.busy = False
        threading.Thread(target=job, daemon=True).start()

    def say(self, m, secs=5):
        self.msg, self.msg_until = m, time.time() + secs

    def _refresh(self):
        self.on = powered()
        self.rows = devices() if self.on is not None else []

    def refresh(self):
        self._bg(self._refresh)

    def scan_now(self):
        def job():
            self.scanning = True
            try:
                scan()
            finally:
                self.scanning = False
            self._refresh()
        self._bg(job)

    def act(self, fn, mac, label):
        def job():
            self.say(label + "...", 40)
            ok, m = fn(mac)
            self.say(m)
            self._refresh()
        self._bg(job)

    def toggle(self):
        def job():
            set_enabled(not bool(self.on))
            self._refresh()
        self._bg(job)


def status_text():
    p = powered()
    if p is None:
        return "No Bluetooth adapter (see /var/log/rgos-bt.log)" if not adapter() else "Starting..."
    return "Bluetooth is on" if p else "Bluetooth is off"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "start":
        sys.exit(start())
    elif cmd == "list":
        for d in devices():
            print(d["mac"], "paired" if d["paired"] else "-", "connected" if d["connected"] else "-", d["name"])
    elif cmd == "scan":
        scan(); print("done")
    elif cmd == "pair" and len(sys.argv) > 2:
        print(pair(sys.argv[2]))
    elif cmd in ("on", "off"):
        set_enabled(cmd == "on")
    else:
        print("usage: btctl.py start | list | scan | pair <mac> | on | off", file=sys.stderr); sys.exit(2)
