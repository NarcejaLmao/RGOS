#!/usr/bin/env python3
"""Write the RG OS initramfs as an uncompressed newc cpio (no cpio tool and no kernel decompressor needed).

usage: mkinitramfs.py <static busybox> <init script> <out.cpio>
Contents: /bin/busybox, /bin/sh, /rgos-init (the script), /dev/console, /dev/null and the empty mount points.
"""
import os
import stat
import sys


def entry(name, mode, data=b"", ino=0, nlink=1, rdev=(0, 0)):
    nm = name.encode() + b"\0"
    hdr = "070701" + "".join("%08X" % v for v in (
        ino, mode, 0, 0, nlink, 0, len(data), 0, 0, rdev[0], rdev[1], len(nm), 0))
    out = hdr.encode() + nm
    out += b"\0" * (-len(out) % 4)
    out += data
    out += b"\0" * (-len(data) % 4)
    return out


def build(busybox, init):
    ents, ino = [], [0]

    def add(name, mode, data=b"", nlink=1, rdev=(0, 0)):
        ino[0] += 1
        ents.append(entry(name, mode, data, ino[0], nlink, rdev))

    for d in ("bin", "sbin", "usr", "usr/bin", "usr/sbin", "dev", "proc", "sys", "run", "tmp", "flash", "sysroot"):
        add(d, stat.S_IFDIR | 0o755, nlink=2)
    add("dev/console", stat.S_IFCHR | 0o600, rdev=(5, 1))
    add("dev/null", stat.S_IFCHR | 0o666, rdev=(1, 3))
    add("bin/busybox", stat.S_IFREG | 0o755, open(busybox, "rb").read())
    add("bin/sh", stat.S_IFLNK | 0o777, b"busybox")
    add("rgos-init", stat.S_IFREG | 0o755, open(init, "rb").read())
    ents.append(entry("TRAILER!!!", 0, ino=0))
    blob = b"".join(ents)
    return blob + b"\0" * (-len(blob) % 512)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    bb, init, out = sys.argv[1:]
    for p in (bb, init):
        if not os.path.isfile(p):
            sys.exit("missing: " + p)
    data = build(bb, init)
    with open(out, "wb") as f:
        f.write(data)
    print("initramfs: %s (%d bytes)" % (out, len(data)))
