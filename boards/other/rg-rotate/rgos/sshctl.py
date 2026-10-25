#!/usr/bin/env python3
"""RG OS SSH / file transfer: a Dropbear SSH server with SFTP (WinSCP, FileZilla, `scp`, `sftp`, `ssh root@<ip>`).

Login:  user root, password "rgos" (the image's default). Change it by putting a file ssh-password.txt (one line) on the
        card's FAT boot partition; it is applied at every boot. To log in with a key instead, put your public key in a file
        named authorized_keys on the same partition.
Files:  /var/lib/rgos/ssh.json   on/off (default on)
        /var/lib/rgos/ssh/       host keys, generated on first start (so the fingerprint stays the same)
        /var/log/rgos-ssh.log    log (copied to rgos-logs.txt on the card)
The ROMS partition is at /media/roms, the boot partition at /boot.
"""
import hashlib, json, os, re, shutil, subprocess, sys, time

CONF = os.environ.get("RGOS_SSH_CONF", "/var/lib/rgos/ssh.json")
KEYDIR = "/var/lib/rgos/ssh"
RUNKEYS = "/run/rgos/ssh"
PIDFILE = "/run/rgos/dropbear.pid"
PORT = 22
B64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def log(msg):
    print("%s rgos-ssh: %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


# ---------------------------------------------------------------- password hash (SHA-512 crypt, "$6$"); Python 3.13 has no crypt module
def sha512_crypt(password, salt, rounds=5000):
    pw, salt = password.encode(), salt.encode()[:16]
    H, n = hashlib.sha512, len(pw)
    b = H(pw + salt + pw).digest()
    a = H(pw + salt + b * (n // 64) + b[:n % 64])
    i = n
    while i:
        a.update(b if i & 1 else pw)
        i >>= 1
    a = a.digest()
    dp = H(pw * n).digest(); p = dp * (n // 64) + dp[:n % 64]
    ds = H(salt * (16 + a[0])).digest(); s = ds * (len(salt) // 64) + ds[:len(salt) % 64]
    c = a
    for r in range(rounds):
        h = H(p if r & 1 else c)
        if r % 3:
            h.update(s)
        if r % 7:
            h.update(p)
        h.update(c if r & 1 else p)
        c = h.digest()
    out = ""
    order = [(0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4), (47, 5, 26), (6, 27, 48), (28, 49, 7), (50, 8, 29),
             (9, 30, 51), (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13), (56, 14, 35), (15, 36, 57), (37, 58, 16),
             (59, 17, 38), (18, 39, 60), (40, 61, 19), (62, 20, 41)]
    for x, y, z in order:
        w = (c[x] << 16) | (c[y] << 8) | c[z]
        out += "".join(B64[(w >> (6 * k)) & 63] for k in range(4))
    w = c[63]
    out += "".join(B64[(w >> (6 * k)) & 63] for k in range(2))
    return "$6$%s$%s" % (salt.decode(), out)


# ---------------------------------------------------------------- settings
def enabled():
    try:
        with open(CONF) as f:
            return bool(json.load(f).get("enabled", True))
    except (OSError, ValueError, AttributeError):
        return True


def pidof_dropbear():
    try:
        pid = int(open(PIDFILE).read().strip())
        if os.path.exists("/proc/%d" % pid) and "dropbear" in open("/proc/%d/comm" % pid).read():
            return pid
    except (OSError, ValueError):
        pass
    return None


def running():
    return pidof_dropbear() is not None


def custom_password_file():
    for p in ("/boot/ssh-password.txt", "/flash/ssh-password.txt"):
        if os.path.exists(p):
            return p
    return None


def info():
    return {"enabled": enabled(), "running": running(), "port": PORT, "user": "root",
            "password": "custom (ssh-password.txt)" if custom_password_file() else "rgos"}


# ---------------------------------------------------------------- start / stop
def apply_password():
    path = custom_password_file()
    if not path:
        return
    try:
        pw = open(path, encoding="utf-8", errors="replace").read().split("\n")[0].strip()
    except OSError:
        return
    if not pw or ":" in pw or len(pw) > 128:
        log("ssh-password.txt ignored (empty, too long, or contains ':')")
        return
    try:
        lines = open("/etc/shadow").read().splitlines()
        new = sha512_crypt(pw, os.urandom(9).hex()[:16])
        out = []
        for l in lines:
            f = l.split(":")
            if f and f[0] == "root" and len(f) > 2:
                f[1] = new
                l = ":".join(f)
            out.append(l)
        os.makedirs("/run/rgos", exist_ok=True)
        tmp = "/run/rgos/shadow"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(out) + "\n")
        os.chmod(tmp, 0o600)
        mounted = any(l.split()[1] == "/etc/shadow" for l in open("/proc/mounts") if len(l.split()) > 1)
        if mounted:
            run(["umount", "/etc/shadow"])
        rc, o = run(["mount", "--bind", tmp, "/etc/shadow"])
        log("custom root password applied" if rc == 0 else "could not apply custom password: %s" % o.strip())
    except OSError as e:
        log("could not apply custom password: %r" % (e,))


def prepare_home():
    """/root is on the read-only system image: give root a RAM home with ~/.ssh/authorized_keys (Dropbear insists on modes)."""
    if not os.path.ismount("/root"):
        run(["mount", "-t", "tmpfs", "-o", "mode=700", "tmpfs", "/root"])
    os.makedirs("/root/.ssh", exist_ok=True)
    os.chmod("/root", 0o700); os.chmod("/root/.ssh", 0o700)
    src = next((p for p in ("/boot/authorized_keys", "/boot/authorized_keys.txt") if os.path.exists(p)), None)
    if src:
        shutil.copyfile(src, "/root/.ssh/authorized_keys")
        os.chmod("/root/.ssh/authorized_keys", 0o600)
        log("authorized_keys installed from %s" % src)


def host_keys():
    os.makedirs(KEYDIR, exist_ok=True)
    os.makedirs(RUNKEYS, exist_ok=True)
    keys = []
    for t in ("ed25519", "ecdsa"):
        persisted, used = os.path.join(KEYDIR, "dropbear_%s_host_key" % t), os.path.join(RUNKEYS, "dropbear_%s_host_key" % t)
        if not os.path.exists(persisted):
            rc, o = run(["dropbearkey", "-t", t, "-f", persisted])
            log("generated %s host key (rc=%s)" % (t, rc))
            if rc != 0:
                continue
        shutil.copyfile(persisted, used)
        os.chmod(used, 0o600)
        keys.append(used)
    return keys


def start():
    if running():
        return True
    os.makedirs("/run/rgos", exist_ok=True)
    apply_password()
    prepare_home()
    keys = host_keys()
    if not keys:
        log("no host keys; cannot start")
        return False
    cmd = ["dropbear", "-p", str(PORT), "-P", PIDFILE, "-j", "-k", "-T", "5"]
    for k in keys:
        cmd += ["-r", k]
    rc, out = run(cmd)
    ok = rc == 0 and running()
    log("dropbear %s %s" % ("started" if ok else "FAILED", out.strip()))
    return ok


def stop():
    pid = pidof_dropbear()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    for _ in range(20):
        if not running():
            break
        time.sleep(0.1)
    return not running()


def set_enabled(on):
    try:
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        with open(CONF, "w") as f:
            json.dump({"enabled": bool(on)}, f)
    except OSError:
        pass
    return start() if on else stop()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "boot":
        sys.exit(0 if (not enabled() or start()) else 1)
    elif cmd == "start":
        sys.exit(0 if start() else 1)
    elif cmd == "stop":
        sys.exit(0 if stop() else 1)
    elif cmd == "status":
        print(json.dumps(info()))
    elif cmd == "hash" and len(sys.argv) > 3:
        print(sha512_crypt(sys.argv[2], sys.argv[3]))
    else:
        print("usage: sshctl.py boot | start | stop | status", file=sys.stderr); sys.exit(2)
