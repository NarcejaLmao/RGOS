#!/usr/bin/env python3
"""Point extlinux at the RG OS initramfs: add an INITRD line after each LINUX/KERNEL line and rdinit=/rgos-init to each
APPEND line, so the kernel runs our init instead of the one built into ROCKNIX's kernel.

usage: patch-extlinux.py <extlinux.conf> <initrd path on the boot partition, e.g. /rgos-initrd.cpio>
Idempotent: earlier INITRD lines and rdinit= options are replaced.
"""
import re
import sys


def patch(text, initrd):
    lines = text.splitlines()
    out, i = [], 0
    kern = re.compile(r"^(\s*)(linux|kernel)(\s+\S.*)$", re.I)
    block_has_append = False
    pending = None            # (indent, upper?) of a kernel line whose block has no APPEND yet

    def flush_missing_append():
        nonlocal pending
        if pending and not block_has_append:
            ind, up = pending
            out.append("%s%s rdinit=/rgos-init" % (ind, "APPEND" if up else "append"))
        pending = None

    for ln in lines:
        low = ln.strip().lower()
        if re.match(r"^label\b", low):
            flush_missing_append()
            block_has_append = False
        if re.match(r"^initrd\b", low):
            continue                                   # replaced below
        m = kern.match(ln)
        if m:
            up = m.group(2).isupper()
            out.append(ln)
            out.append("%s%s %s" % (m.group(1), "INITRD" if up else "initrd", initrd))
            pending = (m.group(1), up)
            continue
        if re.match(r"^append\b", low):
            block_has_append = True
            ln = re.sub(r"\s+rdinit=\S+", "", ln.rstrip()) + " rdinit=/rgos-init"
        out.append(ln)
    flush_missing_append()
    if not any(re.match(r"^\s*initrd\b", l, re.I) for l in out):
        raise SystemExit("no LINUX/KERNEL line found in extlinux.conf; not patched")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    path, initrd = sys.argv[1:]
    new = patch(open(path).read(), initrd)
    open(path, "w").write(new)
    print(new)
