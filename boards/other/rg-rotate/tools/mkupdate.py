#!/usr/bin/env python3
"""Build an RG OS update tar that rgos/rgos-update installs.

usage: mkupdate.py OUT.tar ID DEVICE NAME=PATH [NAME=PATH ...]
  ID      build id, [A-Za-z0-9._-]{1,64}; an update whose id is already installed is skipped on the device
  DEVICE  "rg-rotate" (must match /etc/rgos-device on the device) or "any"
  NAME    a boot-partition file name: SYSTEM, SYSTEM.md5, rgos-initrd.cpio, KERNEL, KERNEL.md5,
          device_trees/ums512-rg-rotate.dtb or extlinux/extlinux.conf
Copy the tar to the update/ folder of the ROMS partition (or the boot partition) and reboot.
"""
import hashlib
import io
import json
import os
import re
import sys
import tarfile

BOOT_FILES = ("device_trees/ums512-rg-rotate.dtb", "KERNEL", "KERNEL.md5", "rgos-initrd.cpio", "SYSTEM", "SYSTEM.md5",
              "extlinux/extlinux.conf")          # keep in sync with rgos/rgos-update


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(argv):
    if len(argv) < 5:
        sys.exit(__doc__)
    out, uid, device = argv[1:4]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", uid):
        sys.exit("bad id %r" % uid)
    files = {}
    for a in argv[4:]:
        name, _, path = a.partition("=")
        if name not in BOOT_FILES:
            sys.exit("not an updatable file: %s (allowed: %s)" % (name, ", ".join(BOOT_FILES)))
        if not os.path.isfile(path):
            sys.exit("missing file for %s: %s" % (name, path))
        files[name] = path
    if not files:
        sys.exit("no files given")
    man = {"format": 1, "id": uid, "device": device, "files": {n: sha256(p) for n, p in files.items()}}
    data = json.dumps(man, indent=1, sort_keys=True).encode()
    with tarfile.open(out, "w", format=tarfile.PAX_FORMAT) as tf:
        ti = tarfile.TarInfo("manifest.json")
        ti.size, ti.mode = len(data), 0o644
        tf.addfile(ti, io.BytesIO(data))
        for name in BOOT_FILES:                  # same order the device replaces them in
            if name in files:
                ti = tf.gettarinfo(files[name], "boot/" + name)
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                ti.mode = 0o644
                with open(files[name], "rb") as f:
                    tf.addfile(ti, f)
    print("update: %s (%d MiB), id %s, device %s: %s" % (out, os.path.getsize(out) >> 20, uid, device, ", ".join(files)))


if __name__ == "__main__":
    main(sys.argv)
