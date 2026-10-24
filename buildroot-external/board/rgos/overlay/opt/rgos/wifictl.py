#!/usr/bin/env python3
"""RG OS Wi-Fi: saved networks, scanning, wpa_supplicant config, status, and the connection daemon.

Used by three things: the menu (Settings > Wi-Fi), the `rgos-wifi` command line tool, and the `rgos-wifi` systemd service
(`rgos-wifi daemon`). Everything is built on the usual userspace tools (wpa_supplicant + wpa_cli, busybox udhcpc, ip,
rfkill) and is discovery based: the adapter is whatever wireless interface the kernel shows in /sys/class/net.

Files:  /var/lib/rgos/wifi.json   saved networks + on/off (mode 600, kept across firmware updates)
        /etc/rgos/wifi.json       defaults: country, module names to try, firmware folder
        /run/rgos/wifi.status     what the daemon sees right now (the menu and `rgos-wifi status` read it)
        /var/log/rgos-wifi.log    daemon, wpa_supplicant and DHCP log
Passwords are never stored: only the 256-bit WPA-PSK derived from SSID + passphrase (still enough to join the network).
"""
import hashlib, json, os, re, shutil, signal, subprocess, sys, threading, time

CONF_FILE = os.environ.get("RGOS_WIFI_CONF", "/var/lib/rgos/wifi.json")
SYS_CONF = os.environ.get("RGOS_WIFI_SYS", "/etc/rgos/wifi.json")
RUN_DIR = os.environ.get("RGOS_RUN", "/run/rgos")
SLEEP_FLAG = os.path.join(RUN_DIR, "sleeping")     # exists while the device sleeps (written by rgos-hw)
STATUS_FILE = os.path.join(RUN_DIR, "wifi.status")
WPA_CONF = os.path.join(RUN_DIR, "wpa_supplicant.conf")
CTRL_DIR = os.environ.get("RGOS_WPA_CTRL", "/run/wpa_supplicant")
WPA_CLI = os.environ.get("RGOS_WPA_CLI", "wpa_cli")
SYSNET = os.environ.get("RGOS_SYSNET", "/sys/class/net")
DHCP_SCRIPT = os.environ.get("RGOS_WIFI_DHCP", "/opt/rgos/rgos-wifi-dhcp")
STALE_S = 10

SYS_DEFAULTS = {"country": "", "iface": "auto", "firmware_dir": "/media/roms/firmware",
                "modules": ["8821cs", "rtl8821cs", "rtw88_8821cs", "8723ds", "rtl8723ds", "8188fu", "xr819", "xradio_wlan",
                            "brcmfmac", "8189fs", "8189es"]}


# ---------------------------------------------------------------- saved networks
def _read_json(path, default):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def load_conf():
    d = _read_json(CONF_FILE, {})
    raw_networks = d.get("networks", [])
    if not isinstance(raw_networks, list):
        raw_networks = []

    nets = []
    max_prio = 0
    for item in raw_networks:
        if not isinstance(item, dict):
            continue
        ssid = item.get("ssid")
        if not isinstance(ssid, str) or not ssid or len(ssid.encode("utf-8", "ignore")) > 32:
            continue

        # Validate persisted values before they reach sorting or wpa_supplicant.
        psk = item.get("psk", "")
        if not isinstance(psk, str) or (psk and not re.fullmatch(r"[0-9a-fA-F]{64}", psk)):
            psk = ""
        try:
            priority = int(item.get("prio", 0))
        except (TypeError, ValueError, OverflowError):
            priority = 0
        priority = max(-2147483648, min(2147483647, priority))
        max_prio = max(max_prio, priority)
        nets.append({"ssid": ssid, "psk": psk, "hidden": bool(item.get("hidden", False)),
                     "prio": priority})

    try:
        saved_prio = int(d.get("prio", 0))
    except (TypeError, ValueError, OverflowError):
        saved_prio = 0
    saved_prio = max(-2147483648, min(2147483647, saved_prio))

    country = d.get("country", "")
    if not isinstance(country, str):
        country = ""
    return {"enabled": bool(d.get("enabled", True)), "prio": max(saved_prio, max_prio),
            "networks": nets, "country": country}


def save_conf(conf):
    try:
        os.makedirs(os.path.dirname(CONF_FILE), exist_ok=True)
        tmp = CONF_FILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(conf, f, indent=1)
        os.replace(tmp, CONF_FILE)
        return True
    except OSError:
        return False


def sys_conf():
    c = dict(SYS_DEFAULTS)
    c.update(_read_json(SYS_CONF, {}))
    return c


def derive_psk(ssid, password):
    """WPA/WPA2-Personal pre-shared key as 64 hex digits (PBKDF2-HMAC-SHA1, 4096 rounds, SSID as salt)."""
    return hashlib.pbkdf2_hmac("sha1", password.encode(), ssid.encode(), 4096, 32).hex()


def password_ok(password):
    return 8 <= len(password.encode()) <= 63


def add_network(ssid, password=None, hidden=False):
    """Save (or replace) a network and make it the preferred one. password None = open network. Returns True if saved."""
    conf = load_conf()
    conf["prio"] += 1
    net = {"ssid": ssid, "psk": derive_psk(ssid, password) if password is not None else "", "hidden": bool(hidden),
           "prio": conf["prio"]}
    conf["networks"] = [n for n in conf["networks"] if n["ssid"] != ssid] + [net]
    return save_conf(conf)


def prefer(ssid):
    conf = load_conf()
    conf["prio"] += 1
    for n in conf["networks"]:
        if n["ssid"] == ssid:
            n["prio"] = conf["prio"]
    return save_conf(conf)


def forget(ssid):
    conf = load_conf()
    conf["networks"] = [n for n in conf["networks"] if n["ssid"] != ssid]
    return save_conf(conf)


def set_enabled(on):
    conf = load_conf()
    conf["enabled"] = bool(on)
    return save_conf(conf)


def make_wpa_conf(conf, country=""):
    """wpa_supplicant.conf text. SSIDs go in as hex and keys as the derived PSK, so no quoting/escaping problems."""
    out = ["ctrl_interface=%s" % CTRL_DIR, "update_config=0", "ap_scan=1"]
    country = (conf.get("country") or country or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", country):
        out.append("country=%s" % country)
    for n in sorted(conf["networks"], key=lambda n: n.get("prio", 0)):
        out += ["", "network={", "    ssid=%s" % n["ssid"].encode().hex()]
        if n.get("hidden"):
            out.append("    scan_ssid=1")
        if n.get("psk"):
            out += ["    key_mgmt=WPA-PSK", "    psk=%s" % n["psk"]]
        else:
            out.append("    key_mgmt=NONE")
        out += ["    priority=%d" % int(n.get("prio", 0)), "}"]
    return "\n".join(out) + "\n"


def write_wpa_conf(conf=None):
    conf = conf or load_conf()
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        fd = os.open(WPA_CONF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(make_wpa_conf(conf, sys_conf().get("country", "")))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- scan results
def unescape_ssid(s):
    """wpa_cli prints SSIDs with \\xNN escapes for non-printable bytes."""
    out, i, b = bytearray(), 0, s.encode("utf-8", "surrogateescape")
    while i < len(b):
        m = re.match(rb"\\x([0-9a-fA-F]{2})", b[i:i + 4])
        if m:
            out.append(int(m.group(1), 16)); i += 4
        elif b[i:i + 2] in (b"\\\\", b'\\"'):
            out.append(b[i + 1]); i += 2
        else:
            out.append(b[i]); i += 1
    return out.decode("utf-8", "replace")


def parse_scan(text):
    """`wpa_cli scan_results` -> [{ssid, rssi, secured, unsupported}] strongest first, one entry per SSID.
    unsupported = WEP or WPA3-only (SAE without PSK), which this build does not join."""
    best = {}
    for line in text.splitlines():
        p = line.split("\t")
        if len(p) < 5 or not re.fullmatch(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", p[0]):
            continue
        ssid = unescape_ssid(p[4])
        if not ssid.strip("\0 "):
            continue                                         # hidden network: no name in the beacon
        try:
            rssi = int(p[2])
        except ValueError:
            continue
        flags = p[3]
        secured = any(k in flags for k in ("WPA", "WEP", "RSN"))
        unsupported = ("WEP" in flags and "WPA" not in flags) or ("SAE" in flags and "PSK" not in flags)
        if ssid not in best or rssi > best[ssid]["rssi"]:
            best[ssid] = {"ssid": ssid, "rssi": rssi, "secured": secured, "unsupported": unsupported}
    return sorted(best.values(), key=lambda n: -n["rssi"])


def bars(rssi):
    return 0 if rssi is None else 4 if rssi >= -55 else 3 if rssi >= -65 else 2 if rssi >= -75 else 1


# ---------------------------------------------------------------- talking to the system
def run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def find_iface():
    try:
        names = sorted(os.listdir(SYSNET))
    except OSError:
        return None
    wl = [n for n in names if os.path.exists(os.path.join(SYSNET, n, "wireless")) or os.path.exists(os.path.join(SYSNET, n, "phy80211"))]
    pref = sys_conf().get("iface", "auto")
    if pref != "auto" and pref in wl:
        return pref
    return wl[0] if wl else None


def wpa(iface, *args, timeout=8):
    return run([WPA_CLI, "-p", CTRL_DIR, "-i", iface, *args], timeout)


def scan(iface, wait=4.0):
    """Trigger a scan and return (networks, error message or '')."""
    rc, out = wpa(iface, "scan")
    if rc != 0 or ("FAIL" in out and "BUSY" not in out):
        return [], "Wi-Fi is not ready yet"
    time.sleep(wait)
    rc, out = wpa(iface, "scan_results")
    return (parse_scan(out), "") if rc == 0 else ([], "Could not read scan results")


def apply():
    """Regenerate the wpa_supplicant config and tell a running supplicant to pick up the change and re-select."""
    ok = write_wpa_conf()
    iface = find_iface()
    if iface and os.path.exists(os.path.join(CTRL_DIR, iface)):
        wpa(iface, "reconfigure")
        wpa(iface, "reassociate")
    return ok


def join(ssid, password=None, hidden=False):
    """Save a new network and connect to it."""
    return add_network(ssid, password, hidden) and apply()


def forget_and_apply(ssid):
    forget(ssid)
    return apply()


def connect(ssid):
    prefer(ssid)
    return apply()


def status():
    """What the daemon last reported: {state: off|no-adapter|disconnected|connecting|connected, ssid, ip, rssi, iface}."""
    try:
        if time.time() - os.path.getmtime(STATUS_FILE) > STALE_S:
            raise OSError
        d = _read_json(STATUS_FILE, {})
    except OSError:
        d = {}
    st = {"state": d.get("state", "off"), "ssid": d.get("ssid", ""), "ip": d.get("ip", ""), "rssi": d.get("rssi"),
          "iface": d.get("iface", "")}
    return st


def status_text(st):
    s = st["state"]
    if s == "connected":
        return "Connected to %s   %s" % (st["ssid"], st["ip"] or "(waiting for an address)")
    return {"off": "Wi-Fi is off", "no-adapter": "No Wi-Fi adapter found (see /var/log/rgos-wifi.log)",
            "connecting": "Connecting...", "disconnected": "Not connected"}.get(s, "Wi-Fi is off")


class Scanner:
    """Background scans for the menu: kick(), then read .nets / .err / .busy. Retries while the supplicant is starting."""
    def __init__(self):
        self.nets, self.err, self.busy, self.seen = [], "", False, False

    def kick(self):
        if self.busy:
            return
        self.busy, self.err = True, ""
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            iface = find_iface()
            if not iface:
                self.nets, self.err = [], "No Wi-Fi adapter found"
                return
            for attempt in range(4):
                nets, err = scan(iface)
                if not err:
                    self.nets, self.err, self.seen = nets, "", True
                    return
                time.sleep(2)
            self.err = err
        finally:
            self.busy = False

    def rows(self, conf):
        """Visible networks plus saved ones that are out of range: [{ssid, rssi, secured, saved, unsupported}]."""
        saved = {n["ssid"]: n for n in conf["networks"]}
        out = [dict(n, saved=n["ssid"] in saved) for n in self.nets]
        seen = {n["ssid"] for n in self.nets}
        for ssid, n in saved.items():
            if ssid not in seen:
                out.append({"ssid": ssid, "rssi": None, "secured": bool(n.get("psk")), "saved": True, "unsupported": False})
        return out


# ---------------------------------------------------------------- the daemon
def log(msg):
    print("%s rgos-wifi: %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def write_status(d):
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        d = dict(d, ts=int(time.time()))
        tmp = STATUS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass


def load_modules(cfg):
    """Best effort: point the kernel's firmware loader at <ROMS>/firmware too, then modprobe the usual Wi-Fi drivers and
    every module the kernel package keeps under a 'wireless' folder."""
    fw = cfg.get("firmware_dir", "")
    if fw:
        try:
            with open("/sys/module/firmware_class/parameters/path", "w") as f:
                f.write(fw)
        except OSError:
            pass
    names = list(cfg.get("modules", []))
    for root, _d, files in os.walk("/lib/modules"):
        if "/wireless" in root:
            names += [re.sub(r"\.ko(\..*)?$", "", f) for f in files if ".ko" in f]
    for m in dict.fromkeys(names):
        run(["modprobe", m], timeout=20)


def wpa_state(iface):
    rc, out = wpa(iface, "status", timeout=4)
    kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l) if rc == 0 else {}
    rssi = None
    if kv.get("wpa_state") == "COMPLETED":
        rc2, o2 = wpa(iface, "signal_poll", timeout=4)
        m = re.search(r"RSSI=(-?\d+)", o2) if rc2 == 0 else None
        rssi = int(m.group(1)) if m else None
    return kv, rssi


def ip_of(iface):
    rc, out = run(["ip", "-4", "-o", "addr", "show", "dev", iface], timeout=4)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else ""


class Daemon:
    def __init__(self):
        self.wpa = self.dhcp = None
        self.last_state = ""
        self.stop = False

    def children_stop(self):
        for attr in ("wpa", "dhcp"):
            p = getattr(self, attr)
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(3)
                except subprocess.TimeoutExpired:
                    p.kill()
            setattr(self, attr, None)

    def start_wpa(self, iface):
        write_wpa_conf()
        try:
            os.remove(os.path.join(CTRL_DIR, iface))                  # stale socket from a killed supplicant
        except OSError:
            pass
        log("starting wpa_supplicant on %s" % iface)
        self.wpa = subprocess.Popen(["wpa_supplicant", "-i", iface, "-c", WPA_CONF, "-D", "nl80211,wext"])

    def start_dhcp(self, iface):
        bb = shutil.which("busybox") or "/bin/busybox"
        log("starting DHCP client on %s" % iface)
        self.dhcp = subprocess.Popen([bb, "udhcpc", "-f", "-i", iface, "-s", DHCP_SCRIPT, "-t", "5", "-T", "3", "-A", "10"])

    def step(self, iface_seen):
        conf, cfg = load_conf(), sys_conf()
        iface = find_iface()
        if not iface:
            self.children_stop()
            if time.time() - iface_seen[0] > 20:                      # drivers can appear late: retry every 20 s
                iface_seen[0] = time.time()
                load_modules(cfg)
            write_status({"state": "no-adapter"})
            return
        if os.path.exists(SLEEP_FLAG):                                # device asleep (rgos-hw): radio stays down, no scanning
            self.children_stop()
            run(["ip", "link", "set", iface, "down"])
            write_status({"state": "off", "iface": iface})
            self.last_state = ""                                      # reconnect (and log it) after waking
            return
        if not conf["enabled"]:
            self.children_stop()
            run(["ip", "link", "set", iface, "down"])
            write_status({"state": "off", "iface": iface})
            return
        run(["rfkill", "unblock", "wifi"])
        run(["ip", "link", "set", iface, "up"])
        if self.wpa is None or self.wpa.poll() is not None:
            self.start_wpa(iface)
            time.sleep(1)
        if self.dhcp is None or self.dhcp.poll() is not None:
            self.start_dhcp(iface)
        kv, rssi = wpa_state(iface)
        ws, ip = kv.get("wpa_state", ""), ip_of(iface)
        if ws == "COMPLETED":
            state = "connected"
        elif ws in ("ASSOCIATING", "ASSOCIATED", "AUTHENTICATING", "4WAY_HANDSHAKE", "GROUP_HANDSHAKE", "SCANNING"):
            state = "connecting" if conf["networks"] and ws != "SCANNING" else "disconnected"
        else:
            state = "disconnected"
        if state == "connected" and self.last_state != "connected" and self.dhcp and self.dhcp.poll() is None:
            self.dhcp.send_signal(signal.SIGUSR1)                      # new link: renew / discover now
        if state != self.last_state:
            log("%s %s %s" % (state, kv.get("ssid", ""), ip))
            self.last_state = state
        write_status({"state": state, "iface": iface, "ssid": kv.get("ssid", "") if state == "connected" else "",
                      "ip": ip if state == "connected" else "", "rssi": rssi})

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop", True))
        seen = [0.0]
        log("daemon start")
        while not self.stop:
            try:
                self.step(seen)
            except Exception as e:                                     # never die: a broken adapter must not stop the device
                log("error: %r" % (e,))
            time.sleep(2)
        self.children_stop()


# ---------------------------------------------------------------- command line
def cli(argv):
    cmd = argv[0] if argv else ""
    if cmd == "daemon":
        Daemon().run()
    elif cmd == "status":
        st = status()
        print(json.dumps(st) if "--json" in argv else status_text(st))
    elif cmd == "scan":
        iface = find_iface()
        if not iface:
            print("no Wi-Fi adapter", file=sys.stderr); return 1
        nets, err = scan(iface)
        if err:
            print(err, file=sys.stderr); return 1
        for n in nets:
            print("%4d dBm  %-6s %s" % (n["rssi"], "secured" if n["secured"] else "open", n["ssid"]))
    elif cmd == "list":
        for n in load_conf()["networks"]:
            print(("hidden " if n.get("hidden") else "") + n["ssid"])
    elif cmd == "connect" and len(argv) >= 2:
        ssid = argv[1]
        pw = next((a for a in argv[2:] if not a.startswith("--")), None)
        if pw is not None and not password_ok(pw):
            print("password must be 8-63 characters", file=sys.stderr); return 1
        if pw is None and ssid in {n["ssid"] for n in load_conf()["networks"]}:
            connect(ssid)
        elif not join(ssid, pw, "--hidden" in argv):
            print("could not save settings", file=sys.stderr); return 1
    elif cmd == "forget" and len(argv) >= 2:
        forget_and_apply(argv[1])
    elif cmd in ("on", "off"):
        set_enabled(cmd == "on")
    elif cmd == "apply":
        return 0 if apply() else 1
    else:
        print("usage: rgos-wifi daemon | status [--json] | scan | list | connect <ssid> [password] [--hidden] | forget <ssid> | on | off | apply",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
