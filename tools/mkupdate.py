#!/usr/bin/env python3
"""Pack a staged boot directory into an RG OS update tar (see overlay/opt/rgos/rgos-update for the format).

usage: mkupdate.py STAGE_DIR OUT.tar --id BUILD_ID [--device rg34xx-sp] [--no-kernel]

STAGE_DIR is the directory build.sh stages for the boot partition (rgos-initrd.gz, rgos.dtb, KERNEL/Image,
extlinux/extlinux.conf). --no-kernel leaves the kernel out (a slimmer tar; the kernel on the card must then match the
modules inside the new initramfs).
"""
import argparse, hashlib, json, os, re, sys, tarfile, tempfile, time

BOOT_FILES = ("rgos.dtb", "KERNEL", "Image", "rgos-initrd.gz", "extlinux/extlinux.conf")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("out")
    ap.add_argument("--id", required=True)
    ap.add_argument("--device", default="any")
    ap.add_argument("--no-kernel", action="store_true")
    a = ap.parse_args()

    # Match the on-device updater's ID rules so bad IDs fail during build,
    # instead of creating an archive that the device will always reject.
    if not ID_RE.fullmatch(a.id):
        ap.error("--id must be 1-64 characters: letters, digits, dot, underscore or hyphen")
    if not a.device or not ID_RE.fullmatch(a.device):
        ap.error("--device must be 1-64 characters: letters, digits, dot, underscore or hyphen")

    names = [n for n in BOOT_FILES if os.path.isfile(os.path.join(a.stage, n))]
    if a.no_kernel:
        names = [n for n in names if n not in ("KERNEL", "Image")]
    for need in ("rgos-initrd.gz", "extlinux/extlinux.conf"):
        if need not in names:
            sys.exit("ERROR: %s missing in %s" % (need, a.stage))
    if "KERNEL" in names and "Image" in names:
        names.remove("Image")

    manifest = {"format": 1, "id": a.id, "device": a.device,
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": {n: sha256(os.path.join(a.stage, n)) for n in names}}
    data = (json.dumps(manifest, indent=2) + "\n").encode()

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    # Use a unique temporary file in the destination directory: concurrent
    # builds must not overwrite each other's partial archive. Always remove it
    # on failure, and atomically publish only a complete tar.
    out_dir = os.path.dirname(os.path.abspath(a.out))
    fd, tmp = tempfile.mkstemp(prefix=".rgos-update-", suffix=".part", dir=out_dir)
    os.close(fd)
    try:
        with tarfile.open(tmp, "w", format=tarfile.GNU_FORMAT) as tf:
            ti = tarfile.TarInfo("manifest.json")
            ti.size, ti.mtime, ti.mode = len(data), int(time.time()), 0o644
            import io
            tf.addfile(ti, io.BytesIO(data))
            for n in names:
                p = os.path.join(a.stage, n)
                if os.path.islink(p) or not os.path.isfile(p):
                    raise ValueError("staged update file is not a regular file: %s" % p)
                ti = tf.gettarinfo(p, arcname="boot/" + n)
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                ti.mode = 0o644
                with open(p, "rb") as f:
                    tf.addfile(ti, f)
        os.replace(tmp, a.out)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    print("Update tar: %s (%.1f MiB)" % (a.out, os.path.getsize(a.out) / 1048576))
    for n in names:
        print("  boot/%-24s %s" % (n, manifest["files"][n][:16]))
    print("  id %s, device %s" % (a.id, a.device))


if __name__ == "__main__":
    main()
