#!/usr/bin/env python3
"""RG OS second SD card support (devices with two card slots).

The card RG OS boots from (the disk that holds the root partition) is "card 1": its ROMS partition is mounted on
/media/roms by fstab and holds saves, states, BIOS files, scraper media and settings. Any OTHER mmcblk disk is "card 2":
this module mounts its best partition on /media/roms2 and the menu lists the games in <card 2>/roms/<system>/ together
with the games of card 1 (same folder names: roms/gba, roms/nes, ..., roms/ports).

Nothing here assumes a device name: the kernel numbers cards (mmcblk0, mmcblk1, ...) in the order it finds them, so
either slot may end up as mmcblk0. The boot card is found from what is mounted on / (and /boot, /media/roms); every other
mmcblk disk is a candidate. On a device with one slot there is simply never a second disk and nothing happens.

Saves, states, screenshots and scraper media always go to card 1 (RetroArch's directories do not change), so pulling
card 2 never takes progress with it.

Commands (rgos-sdcard):
  sync      mount card 2 if there is one, unmount it if it is gone, fix a wrong ROMS mount (default; udev and boot call this)
  status    print what is mounted (JSON), exit 1 if there is no card 2
  roots     print the extra ROM roots, one per line (for scripts)
  bootdisk  print the boot card's disk name (mmcblk0 or mmcblk1) for scripts that need its partitions
Settings: /etc/rgos/sdcard.json (your own overrides in /var/lib/rgos/sdcard.json, kept across updates). Log: /var/log/rgos-sdcard.log.
"""
import fcntl, json, os, re, subprocess, sys, time

CONF_FILES = [os.environ.get("RGOS_SDCARD_CONF", "/etc/rgos/sdcard.json"), "/var/lib/rgos/sdcard.json"]
DEFAULTS = {"enabled": True,
            "mountpoint": "/media/roms2",
            "fs": ["exfat", "vfat", "ext4", "ntfs"],     # filesystems that may be mounted, best first is not implied: the largest partition wins
            "min_mb": 8,                                 # smaller partitions (boot/EFI stubs) are ignored
            "create_folders": True,                      # make roms/<system>/ on a card that has none yet
            "readonly": False}                           # mount card 2 read-only
STATUS_FILE = os.environ.get("RGOS_SDCARD_STATUS", "/run/rgos/sdcard2.json")
LOCK_FILE = os.environ.get("RGOS_SDCARD_LOCK", "/run/rgos/sdcard.lock")
LOG_FILE = os.environ.get("RGOS_SDCARD_LOG", "/var/log/rgos-sdcard.log")
PROC_MOUNTS = "/proc/mounts"
SYS_BLOCK = "/sys/block"
SYS_DEV_BLOCK = "/sys/dev/block"
DEV_DIR = "/dev"
PRIMARY_MOUNT = "/media/roms"
SYSTEM_MOUNTS = ("/", "/boot", PRIMARY_MOUNT)     # whatever disk backs these is card 1
SKIP_LABELS = ("RGBOOT", "rgos-root")             # partitions of an RG OS system card are never game storage
# Folder names under roms/ (kept in step with menu.SYSTEMS and rgos-session)
SYSTEM_DIRS = ("gba", "nes", "snes", "gb", "gbc", "genesis", "mastersystem", "gamegear", "sg1000", "segacd", "sega32x", "segapico", "atari2600", "atari5200", "atari7800", "ngp", "wonderswan", "arcade", "neogeo", "ports")
MOUNT_TYPES = {"exfat": ("exfat",), "vfat": ("vfat",), "ext4": ("ext4",), "ntfs": ("ntfs3", "ntfs")}
MOUNT_OPTS = {"exfat": "noatime,umask=000", "vfat": "noatime,umask=000", "ext4": "noatime", "ntfs3": "noatime,umask=000", "ntfs": "noatime,umask=000"}
DISK_RE = re.compile(r"^mmcblk\d+$")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def sh(args, timeout=15):
    """Run a command; returns (returncode, stdout). Never raises (timeouts and missing programs count as failure)."""
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, universal_newlines=True)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "%s" % e


def load_conf():
    conf = dict(DEFAULTS)
    for path in CONF_FILES:
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                conf.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return conf


# ---------------------------------------------------------------- what is mounted

def mounts():
    """[(source, mountpoint, fstype, options)] from /proc/mounts (octal escapes such as \\040 decoded)."""
    out = []
    try:
        with open(PROC_MOUNTS) as f:
            for line in f:
                p = line.split()
                if len(p) >= 4:
                    dec = lambda s: re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)
                    out.append((dec(p[0]), dec(p[1]), p[2], p[3]))
    except OSError:
        pass
    return out


def disk_of(name):
    """'/dev/mmcblk1p2' -> 'mmcblk1', 'mmcblk0' -> 'mmcblk0', anything else -> None."""
    m = re.match(r"^(?:/dev/)?(mmcblk\d+)(?:p\d+)?$", name or "")
    return m.group(1) if m else None


def _disk_of_path(path):
    """The mmcblk disk behind a mounted path, through its device number (works when /proc/mounts says /dev/root)."""
    try:
        st = os.stat(path)
        real = os.path.realpath("%s/%d:%d" % (SYS_DEV_BLOCK, os.major(st.st_dev), os.minor(st.st_dev)))
    except OSError:
        return None
    if os.path.exists(os.path.join(real, "partition")):
        real = os.path.dirname(real)
    d = os.path.basename(real)
    return d if DISK_RE.match(d) else None


def disk_backing(mountpoint, table=None):
    """The mmcblk disk mounted on mountpoint, or None."""
    for src, mnt, _fs, _o in (mounts() if table is None else table):
        if mnt == mountpoint and disk_of(src):
            return disk_of(src)
    return _disk_of_path(mountpoint) if any(m[1] == mountpoint for m in (mounts() if table is None else table)) else None


def boot_disk(table=None):
    """The card the system runs from: the disk behind /, else /boot, else mmcblk0."""
    table = mounts() if table is None else table
    for mp in ("/", "/boot"):
        d = disk_backing(mp, table)
        if d:
            return d
    return "mmcblk0"


# ---------------------------------------------------------------- finding the card

def _num(name):
    return int(re.sub(r"\D", "", name) or 0)


def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def candidate_disks(table=None):
    """mmcblk disks that are not the boot card (and not the disk behind /media/roms), lowest number first."""
    table = mounts() if table is None else table
    system = {d for d in (disk_backing(mp, table) for mp in SYSTEM_MOUNTS) if d}
    system.add(boot_disk(table))
    try:
        names = os.listdir(SYS_BLOCK)
    except OSError:
        return []
    out = []
    for d in sorted((n for n in names if DISK_RE.match(n)), key=_num):
        if d in system or _read_int(os.path.join(SYS_BLOCK, d, "size")) <= 0:
            continue
        out.append(d)
    return out


def partitions(disk):
    """[(device path, size in sectors)] of a disk's partitions; the whole disk if it has none (card formatted without a table)."""
    parts = []
    try:
        for n in os.listdir(os.path.join(SYS_BLOCK, disk)):
            if re.fullmatch(re.escape(disk) + r"p\d+", n):
                parts.append((n, _read_int(os.path.join(SYS_BLOCK, disk, n, "size"))))
    except OSError:
        pass
    if not parts:
        return [(os.path.join(DEV_DIR, disk), _read_int(os.path.join(SYS_BLOCK, disk, "size")))]
    return [(os.path.join(DEV_DIR, n), s) for n, s in sorted(parts, key=lambda x: _num(x[0].rsplit("p", 1)[1]))]


def probe(dev):
    """blkid -o export of a device as a dict (TYPE, LABEL, ...); {} if it has no recognisable filesystem."""
    rc, out = sh(["blkid", "-o", "export", dev], 8)
    info = {}
    if rc == 0:
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip()] = v.strip()
    return info


def pick_partition(disk, conf):
    """The partition of `disk` to use as game storage, or None. A partition labelled ROMS wins, else the biggest usable one."""
    best = None
    for dev, sectors in partitions(disk):
        if sectors * 512 < conf["min_mb"] * 1048576:
            continue
        info = probe(dev)
        fs, label = info.get("TYPE", ""), info.get("LABEL", "")
        if fs not in conf["fs"] or fs not in MOUNT_TYPES or label in SKIP_LABELS:
            continue
        cand = {"dev": dev, "disk": disk, "fstype": fs, "label": label, "sectors": sectors}
        key = (label.upper() == "ROMS", sectors)
        if best is None or key > best[0]:
            best = (key, cand)
    return best[1] if best else None


# ---------------------------------------------------------------- mounting

def mount_dev(dev, fstype, mountpoint, readonly=False):
    """Mount dev on mountpoint. Returns (ok, read_only). Falls back to a read-only mount if read-write fails."""
    os.makedirs(mountpoint, exist_ok=True)
    for mod in {"exfat": "exfat", "vfat": "vfat", "ntfs": "ntfs3"}.get(fstype, "").split():
        sh(["modprobe", mod], 10)                      # harmless when built in or already loaded
    for ro in ([True] if readonly else [False, True]):
        for t in MOUNT_TYPES[fstype]:
            opts = MOUNT_OPTS[t] + (",ro" if ro else "")
            rc, out = sh(["mount", "-t", t, "-o", opts, dev, mountpoint], 20)
            if rc == 0:
                return True, ro
            log("mount -t %s -o %s %s %s failed (rc=%s): %s" % (t, opts, dev, mountpoint, rc, out.strip()[:200]))
    return False, False


def umount(mountpoint):
    rc, out = sh(["umount", mountpoint], 15)
    if rc != 0:                                        # busy (a game is running from it) or already half gone: detach lazily
        sh(["umount", "-l", mountpoint], 15)


def make_folders(mountpoint):
    base = os.path.join(mountpoint, "roms")
    for s in SYSTEM_DIRS:
        try:
            os.makedirs(os.path.join(base, s), exist_ok=True)
        except OSError:
            return False
    return True


def write_status(info):
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        if info is None:
            if os.path.exists(STATUS_FILE):
                os.unlink(STATUS_FILE)
            return
        tmp = STATUS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(info, f)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass


def heal_primary(table, conf):
    """If LABEL=ROMS resolved to a partition of another card (two RG OS cards both carry that label) put the ROMS of the
    boot card back on /media/roms. Only acts when the boot card really has a ROMS partition; never formats anything."""
    cur = disk_backing(PRIMARY_MOUNT, table)
    boot = boot_disk(table)
    if cur is None or cur == boot:
        return False
    for dev, sectors in partitions(boot):
        info = probe(dev)
        if info.get("LABEL") == "ROMS" and info.get("TYPE") in MOUNT_TYPES:
            log("/media/roms is on %s, not on the boot card %s: remounting %s" % (cur, boot, dev))
            umount(PRIMARY_MOUNT)
            ok, _ro = mount_dev(dev, info["TYPE"], PRIMARY_MOUNT)
            return ok
    return False


def sync():
    """Make the mount state match the cards that are inserted. Safe to run at any time, from any number of callers."""
    conf = load_conf()
    mp = conf["mountpoint"]
    table = mounts()
    if heal_primary(table, conf):
        table = mounts()
    mounted = next((m for m in table if m[1] == mp), None)
    want = None
    if conf["enabled"]:
        for d in candidate_disks(table):
            want = pick_partition(d, conf)
            if want:
                break
    if mounted and want and os.path.basename(mounted[0]) == os.path.basename(want["dev"]):
        pass                                           # already right
    else:
        if mounted:
            log("unmounting %s (%s): %s" % (mp, mounted[0], "card removed or changed" if want else "no card 2"))
            umount(mp)
            mounted = None
        if want:
            ok, ro = mount_dev(want["dev"], want["fstype"], mp, bool(conf["readonly"]))
            if ok:
                want["ro"] = ro
                log("card 2: %s (%s%s) mounted on %s" % (want["dev"], want["fstype"], ", read-only" if ro else "", mp))
            else:
                log("card 2: could not mount %s (%s)" % (want["dev"], want["fstype"]))
                want = None
    if want:
        if conf["create_folders"] and not want.get("ro") and not os.path.isdir(os.path.join(mp, "roms")):
            make_folders(mp)
        st = {"mount": mp, "dev": want["dev"], "disk": want["disk"], "fstype": want["fstype"], "label": want["label"],
              "ro": bool(want.get("ro", False)), "time": int(time.time())}
        if st["dev"] == (mounted or ("",))[0] and os.path.exists(STATUS_FILE):
            old = load_status_file()
            if old and old.get("dev") == st["dev"]:
                st["ro"] = old.get("ro", st["ro"])
        write_status(st)
    else:
        write_status(None)
        try:
            os.rmdir(mp)                               # an empty mount point of a card that is gone; harmless if it fails
        except OSError:
            pass
    return want


# ---------------------------------------------------------------- for the menu

def load_status_file():
    try:
        with open(STATUS_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def status():
    """The card-2 record from the last sync if that card is still mounted, else None."""
    st = load_status_file()
    if not st or not st.get("mount"):
        return None
    if not any(m[1] == st["mount"] for m in mounts()):
        return None
    return st


def extra_roots():
    """Mount points of additional ROM cards whose roms/ folder can be listed (today: at most card 2)."""
    st = status()
    return [st["mount"]] if st else []


def usage(mountpoint):
    """(free_bytes, total_bytes) of a mounted card or None."""
    try:
        s = os.statvfs(mountpoint)
        return s.f_bavail * s.f_frsize, s.f_blocks * s.f_frsize
    except OSError:
        return None


# ---------------------------------------------------------------- command line

def cli(argv):
    cmd = argv[0] if argv else "sync"
    if cmd == "status":
        st = status()
        print(json.dumps(st) if st else "no second SD card")
        return 0 if st else 1
    if cmd == "bootdisk":
        print(boot_disk())
        return 0
    if cmd == "roots":
        for r in extra_roots():
            print(r)
        return 0
    if cmd != "sync":
        print("usage: rgos-sdcard [sync|status|roots|bootdisk]", file=sys.stderr)
        return 2
    try:
        os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
        lock = open(LOCK_FILE, "w")
        fcntl.flock(lock, fcntl.LOCK_EX)               # udev can fire several events at once; one sync at a time
    except OSError:
        lock = None
    if "--settle" in argv:
        sh(["udevadm", "settle", "--timeout=5"], 8)    # let the card's partitions appear before looking
    try:
        sync()
    except Exception as e:                             # a card problem must never break the boot or the menu
        log("sync failed: %r" % (e,))
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
