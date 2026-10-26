#!/usr/bin/env bash
# Prints what's needed to see how the ROCKNIX kernel boots: extlinux.conf, boot partition listing,
# the kernel's built-in initramfs file list, and its /init script.
# Usage: sudo bash inspect-kernel.sh [image]     (default: out/rg-rotate-rgos.img)
set -euo pipefail
IMG="${1:-out/rg-rotate-rgos.img}"
[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }
M="$(mktemp -d)"; L="$(losetup -Pf --show -r "$IMG")"
trap 'umount "$M" 2>/dev/null; losetup -d "$L"; rmdir "$M"' EXIT
mount -o ro "${L}p1" "$M"
echo "===== boot partition ====="; ls -la "$M"
echo "===== extlinux.conf ====="; cat "$M"/extlinux/extlinux.conf 2>/dev/null || cat "$M"/extlinux.conf
KERN="$(awk 'tolower($1)=="linux"||tolower($1)=="kernel" {print $2; exit}' "$M"/extlinux/extlinux.conf 2>/dev/null || true)"
KERN="$M/${KERN:-/KERNEL}"; [[ -f "$KERN" ]] || KERN="$M/KERNEL"
echo "===== kernel: $KERN ($(stat -c %s "$KERN") bytes) ====="
python3 - "$KERN" <<'PY'
import sys, zlib, lzma, subprocess, struct, re
data = open(sys.argv[1], 'rb').read()

def parse_cpio(buf):
    out, pos = {}, 0
    while pos + 110 <= len(buf) and buf[pos:pos+6] == b'070701':
        f = [int(buf[pos+6+8*i:pos+14+8*i], 16) for i in range(13)]
        mode, filesize, namesize = f[1], f[6], f[11]
        name = buf[pos+110:pos+110+namesize-1].decode('latin1')
        pos = (pos + 110 + namesize + 3) & ~3
        content = buf[pos:pos+filesize]
        pos = (pos + filesize + 3) & ~3
        if name == 'TRAILER!!!': return out
        out[name] = (mode, content)
    return out if out else None

def try_decomp(off):
    chunk = data[off:]
    for fn in (
        lambda: zlib.decompressobj(31).decompress(chunk),
        lambda: lzma.LZMADecompressor(format=lzma.FORMAT_XZ).decompress(chunk),
        lambda: subprocess.run(['zstd','-dc'], input=chunk, capture_output=True).stdout,
        lambda: subprocess.run(['lz4','-dc'], input=chunk, capture_output=True).stdout,
        lambda: subprocess.run(['bzip2','-dc'], input=chunk, capture_output=True).stdout,
    ):
        try:
            d = fn()
            if d[:6] == b'070701': return d
        except Exception: pass
    return None

found = None
MAGIC = b'|'.join(re.escape(x) for x in (b'\x1f\x8b\x08', b'\xfd7zXZ\x00', b'\x28\xb5\x2f\xfd', b'\x02\x21\x4c\x18', b'BZh'))
for m in re.finditer(MAGIC, data):
    d = try_decomp(m.start())
    if d:
        p = parse_cpio(d)
        if p and any(n in p for n in ('init', './init', 'sbin/init')) or (p and len(p) > 5):
            found = (m.start(), 'compressed', p); break
if not found:
    for m in re.finditer(b'070701', data):
        p = parse_cpio(data[m.start():])
        if p and len(p) > 5: found = (m.start(), 'raw', p); break
if not found:
    print("Could not find an embedded initramfs in the kernel (may be none)."); sys.exit()
off, kind, files = found
print(f"Embedded initramfs at offset {off} ({kind}), {len(files)} entries")
print("--- file list (first 150) ---")
for n in sorted(files)[:150]:
    mode = files[n][0]
    t = 'l' if (mode & 0o170000) == 0o120000 else 'd' if (mode & 0o170000) == 0o040000 else '-'
    extra = ' -> ' + files[n][1].decode('latin1') if t == 'l' else ''
    print(t, n + extra)
for cand in ('init', './init', '/init'):
    if cand in files:
        mode, c = files[cand]
        print(f"--- /init ({'symlink -> ' + c.decode() if (mode & 0o170000)==0o120000 else str(len(c))+' bytes'}) ---")
        if (mode & 0o170000) != 0o120000: print(c.decode('latin1')[:12000])
        break
for n in files:
    if re.search(r'(^|/)(init|functions|config|options)$|\.conf$', n) and n not in ('init','./init'):
        pass
PY
echo "===== kernel IKCONFIG (initramfs/cmdline related), if embedded ====="
python3 - "$KERN" <<'PY'
import sys, zlib, re
d = open(sys.argv[1],'rb').read()
i = d.find(b'IKCFG_ST')
if i < 0: print("no embedded config"); sys.exit()
try:
    cfg = zlib.decompressobj(31).decompress(d[i+8:]).decode()
    for l in cfg.splitlines():
        if re.search(r'INITRAMFS|CMDLINE|BLK_DEV_INITRD|DRM_SPRD|SPRD|FRAMEBUFFER_CONSOLE|FB_SIMPLE|DRM_FBDEV', l) and not l.startswith('# '):
            print(l)
except Exception as e: print("config decode failed:", e)
PY
