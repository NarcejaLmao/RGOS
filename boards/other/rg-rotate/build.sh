#!/usr/bin/env bash
# rg-rotate-rgos: Debian + RG OS launcher for the Anbernic RG Rotate (Unisoc T618)
# Takes the stock ROCKNIX-T618 SD image and replaces ONLY its SYSTEM squashfs with a bare-bones
# Debian arm64 that starts the RG OS menu (white screen only as a fallback). The partition table, bootloader area, kernel
# and DTB are left byte-for-byte alone. ROCKNIX's init (which waits 60 s and prints "NEVER TOUCH boot=...")
# is bypassed by a tiny RG OS initramfs (initramfs/) loaded through extlinux.conf; extlinux.conf.stock keeps the original.
#
# Run as root on Debian/Ubuntu (WSL2 is fine):
#   (from the repo root: ./build.sh rg-rotate [image|update])
#   sudo ./build.sh          full SD card image  -> out/rg-rotate-rgos.img
#   sudo ./build.sh update   offline update tar  -> out/rgos-update-<id>.tar  (copy it to update/ on the ROMS partition and reboot)
set -euo pipefail
MODE="${1:-image}"
[[ "$MODE" == "image" || "$MODE" == "update" ]] || { echo "usage: sudo ./build.sh [image|update]"; exit 1; }

ROCKNIX_URL="${ROCKNIX_URL:-https://github.com/beebono/distribution/releases/download/beta-20260818/ROCKNIX-T618.aarch64-20260818.img.gz}"
DEBIAN_SUITE="${DEBIAN_SUITE:-trixie}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://deb.debian.org/debian}"
PSX_REPO="${PSX_REPO:-https://github.com/libretro/pcsx_rearmed.git}"   # PlayStation core (pcsx_rearmed, built from source); PSX_REPO=none skips it
NGP_SRC="${NGP_SRC:-https://github.com/libretro/beetle-ngp-libretro/archive/refs/heads/master.tar.gz}"   # Neo Geo Pocket core (mednafen_ngp); NGP_SRC=none skips it
# Emulation: RetroArch (aarch64) + Gambatte (Game Boy / Game Boy Color) + mGBA (Game Boy Advance, also a GB/GBC alternative),
# with Mesa (Panfrost) for KMS output and SDL2 for input.
# Also: Nestopia (NES / Famicom, Debian package libretro-nestopia), Snes9x (SNES; Debian non-free, hence --components below) and Beetle WonderSwan (WS / WSC). libretro-beetle-psx is NOT in EMU_PKGS:
# a package missing from the suite aborts debootstrap, so it is installed best-effort later (PlayStation uses pcsx_rearmed, built from source). The Neo Geo Pocket core has no Debian
# package: it is compiled from source in the rootfs below (NGP_SRC), or drop a prebuilt aarch64 *_libretro.so into cores/ next to build.sh.
# More libretro cores: EXTRA_PKGS="libretro-genesisplusgx" sudo -E ./build.sh  (Debian package names)
EMU_PKGS="retroarch,libretro-gambatte,libretro-mgba,libretro-snes9x,libretro-nestopia,libretro-beetle-wswan,libretro-core-info,libgl1-mesa-dri,libegl1,libegl-mesa0,libgles2,libgbm1,libsdl2-2.0-0"
EXTRA_PKGS="${EXTRA_PKGS:-}"
# The exFAT ROMS partition (partition 4) is NOT in the image: rgos/rgos-roms creates it on the device's first boot, using all the
# free space on the card, and formats it exFAT.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$HERE/work"; OUT="$HERE/out"; IMG="$OUT/rg-rotate-rgos.img"
[[ "$MODE" == "update" ]] && IMG="$WORK/update-scratch.img"   # an update build never overwrites a finished SD card image
ROOTFS="$WORK/rootfs"

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo ./build.sh)"; exit 1; }
need=(debootstrap losetup mksquashfs unsquashfs wget gunzip md5sum python3 readelf sfdisk truncate fatlabel)
[[ "$MODE" == "image" ]] && need+=(sgdisk)
[[ "$(uname -m)" == "aarch64" ]] || need+=(qemu-aarch64-static)
for t in "${need[@]}"; do
  command -v "$t" >/dev/null || { echo "Missing tool: $t"
    echo "apt install debootstrap qemu-user-static binfmt-support squashfs-tools wget gdisk dosfstools"; exit 1; }
done

mkdir -p "$WORK" "$OUT" "$WORK/mnt"
RX_GZ="$WORK/$(basename "$ROCKNIX_URL")"; RX_IMG="${RX_GZ%.gz}"
LOOP=""
cleanup() { set +e; mountpoint -q "$WORK/mnt" && umount "$WORK/mnt"; [[ -n "$LOOP" ]] && losetup -d "$LOOP"; }
trap cleanup EXIT

# 1. ROCKNIX image ------------------------------------------------------------
if [[ ! -f "$RX_IMG" ]]; then
  [[ -f "$RX_GZ" ]] || { echo "==> Downloading ROCKNIX-T618"; wget -O "$RX_GZ" "$ROCKNIX_URL"; }
  gunzip -k -f "$RX_GZ"
fi
echo "==> Copying stock image to $IMG"
cp --sparse=always "$RX_IMG" "$IMG"
LOOP="$(losetup -Pf --show "$IMG")"
# The FAT boot partition (partition 1) is labelled ROCKNIX in the stock image; RG OS calls it RGOS.
echo "==> Renaming the boot partition label ROCKNIX -> RGOS"
fatlabel "${LOOP}p1" RGOS
mount "${LOOP}p1" "$WORK/mnt"
echo "==> Stock boot partition:"; ls -la "$WORK/mnt"
echo "==> Stock extlinux config (left untouched):"
cat "$WORK/mnt"/extlinux/extlinux.conf 2>/dev/null || cat "$WORK/mnt"/extlinux.conf 2>/dev/null || echo "(none found)"

OLD_SYS="$WORK/mnt/SYSTEM"
[[ -f "$OLD_SYS" ]] || { echo "!! No SYSTEM file on the boot partition. Paste 'ls -la' above to me."; exit 1; }
COMP="$(unsquashfs -s "$OLD_SYS" | awk '/^Compression/ {print $2; exit}')"; COMP="${COMP:-gzip}"
BSIZE="$(unsquashfs -s "$OLD_SYS" | awk '/^Block size/ {print $3; exit}')"; BSIZE="${BSIZE:-131072}"
echo "==> Stock SYSTEM uses squashfs compression: $COMP, block size $BSIZE (matching it)"

# 2. harvest kernel modules + firmware from stock SYSTEM -----------------------
rm -rf "$WORK/system"
echo "==> Unpacking stock SYSTEM (for modules/firmware)"
unsquashfs -q -d "$WORK/system" "$OLD_SYS" || echo "   (some unsquashfs errors; continuing)"

# 3. Debian rootfs -------------------------------------------------------------
rm -rf "$ROOTFS"
echo "==> debootstrap $DEBIAN_SUITE arm64 minbase"
debootstrap --arch=arm64 --variant=minbase --foreign --components=main,non-free --include=busybox-static,kmod,python3,python3-pygame,fonts-dejavu-core,e2fsprogs,exfatprogs,fdisk,gdisk,iproute2,iw,wpasupplicant,rfkill,alsa-utils,bluez,dbus,dropbear-bin,openssh-sftp-server,"$EMU_PKGS${EXTRA_PKGS:+,${EXTRA_PKGS// /,}}" \
  "$DEBIAN_SUITE" "$ROOTFS" "$DEBIAN_MIRROR"
[[ "$(uname -m)" == "aarch64" ]] || cp "$(command -v qemu-aarch64-static)" "$ROOTFS/usr/bin/"
# no package may start a daemon (dbus, bluez, dropbear) inside the build chroot
mkdir -p "$ROOTFS/usr/sbin"; printf '#!/bin/sh\nexit 101\n' > "$ROOTFS/usr/sbin/policy-rc.d"; chmod +x "$ROOTFS/usr/sbin/policy-rc.d"
chroot "$ROOTFS" /debootstrap/debootstrap --second-stage
rm -f "$ROOTFS/usr/sbin/policy-rc.d"
echo rg-rotate-rgos > "$ROOTFS/etc/hostname"

# SSH / SFTP (Dropbear): root login with the default password "rgos" (override: ssh-password.txt on the boot partition).
# Host keys are generated per device on first start (rgos/sshctl.py), never shipped in the image.
echo "root:rgos" | chroot "$ROOTFS" chpasswd || echo "!! could not set the default root password"
rm -f "$ROOTFS"/etc/dropbear/dropbear_*_host_key "$ROOTFS"/etc/dropbear/dropbear_*_host_key.pub 2>/dev/null
# Bluetooth: let controllers that reconnect without a fresh bond (some pads after a power cycle) connect
mkdir -p "$ROOTFS/etc/bluetooth"
printf '[General]\nClassicBondedOnly=false\n' > "$ROOTFS/etc/bluetooth/input.conf"

MODDIR="$(find "$WORK/system" -maxdepth 7 -type d -path '*/lib/modules/*' -name '[0-9]*' -prune 2>/dev/null | head -1 || true)"
if [[ -n "$MODDIR" ]]; then
  KVER="$(basename "$MODDIR")"; echo "==> Installing modules for $KVER"
  mkdir -p "$ROOTFS/lib/modules"; cp -a "$MODDIR" "$ROOTFS/lib/modules/$KVER"
  chroot "$ROOTFS" depmod -a "$KVER" || true
fi
# ROCKNIX keeps modules and firmware under usr/lib/kernel-overlays/base/lib/ (6-7 levels deep), so search deep, and also look next to the
# modules directory; take the candidate with the most files. Without these the Wi-Fi/Bluetooth/audio DSP firmware requests all fail.
FWDIR=""; FWBEST=0
for c in $(find "$WORK/system" -maxdepth 9 -type d -path '*lib/firmware' 2>/dev/null) ${MODDIR:+"$(dirname "$(dirname "$MODDIR")")/firmware"}; do
  [[ -d "$c" ]] || continue
  n=$(find "$c" -type f 2>/dev/null | wc -l); (( n > FWBEST )) && { FWBEST=$n; FWDIR="$c"; }
done
echo "==> Firmware directory: ${FWDIR:-NONE FOUND} ($FWBEST files)"
[[ -n "$FWDIR" ]] && { mkdir -p "$ROOTFS/lib/firmware"; cp -a "$FWDIR"/. "$ROOTFS/lib/firmware/"; }
# the Unisoc BSP drivers look in /system/etc/firmware (WCN modem, audio DSP): point it at the same files
mkdir -p "$ROOTFS/system/etc"; ln -sfn /lib/firmware "$ROOTFS/system/etc/firmware"
# What the stock kernel offers for Wi-Fi / Bluetooth: printed here and shipped as /etc/rgos/wireless-modules.txt, so
# rgos-logs.txt on the card can say which driver should have loaded when "No Wi-Fi adapter found" shows.
mkdir -p "$ROOTFS/etc/rgos"
if [[ -n "${MODDIR:-}" ]]; then
  find "$MODDIR" -type f -name '*.ko*' 2>/dev/null | grep -i -E '/(wireless|bluetooth)/|sprd|uwe|wcn|sdiohal|rtw|rtl8|brcm|aic8|cfg80211|mac80211|btusb|hci_uart' \
    | sed -E 's#.*/##; s#\.ko(\..*)?$##' | sort -u > "$ROOTFS/etc/rgos/wireless-modules.txt" || true
fi
echo "==> Wi-Fi/Bluetooth modules in the stock kernel: $(tr '\n' ' ' < "$ROOTFS/etc/rgos/wireless-modules.txt" 2>/dev/null)"
[[ -s "$ROOTFS/etc/rgos/wireless-modules.txt" ]] || echo "!! no wireless modules found in the stock SYSTEM: the driver is built into the kernel (needs firmware only) or MODDIR is wrong"
echo "==> Wi-Fi-looking firmware: $(ls "$ROOTFS/lib/firmware" 2>/dev/null | grep -i -E 'wcn|sprd|rtl|rtw|brcm|aic|mt7|ath|wifi|wlan|uwe|sc2' | tr '\n' ' ')"

# RG OS: launcher files, device id, and the init that starts it ----------------------------
echo "==> Installing RG OS into /opt/rgos"
HERE_RGOS="$HERE/rgos"
[[ -f "$HERE_RGOS/menu.py" ]] || { echo "!! $HERE_RGOS/menu.py missing"; exit 1; }
mkdir -p "$ROOTFS/opt/rgos" "$ROOTFS/etc/rgos" "$ROOTFS/var/lib/rgos" "$ROOTFS/var/log" "$ROOTFS/media/roms" "$ROOTFS/boot"
# ROCKNIX's initramfs moves its /flash and /storage mounts into the new root; give it somewhere to put them
mkdir -p "$ROOTFS/flash" "$ROOTFS/storage" "$ROOTFS/run" "$ROOTFS/tmp" "$ROOTFS/root"
find "$HERE_RGOS" -maxdepth 1 -type f -exec cp {} "$ROOTFS/opt/rgos/" \;   # files only (ignores __pycache__ etc.)
chmod 755 "$ROOTFS"/opt/rgos/rgos-* "$ROOTFS/opt/rgos/menu.py" "$ROOTFS/opt/rgos/wifictl.py" "$ROOTFS/opt/rgos/btctl.py" "$ROOTFS/opt/rgos/sshctl.py"
# the services the session starts must really be in the image
for f in usr/sbin/dropbear usr/bin/dropbearkey usr/lib/openssh/sftp-server usr/bin/bluetoothctl usr/bin/dbus-daemon sbin/mkfs.ext4 sbin/mkfs.exfat usr/sbin/sgdisk usr/sbin/partx sbin/wpa_supplicant; do
  [[ -e "$ROOTFS/$f" || -e "$ROOTFS/usr/$f" ]] || echo "!! $f is missing from the image"
done
ls "$ROOTFS"/usr/libexec/bluetooth/bluetoothd "$ROOTFS"/usr/lib/bluetooth/bluetoothd >/dev/null 2>&1 || echo "!! bluetoothd is missing from the image"
echo "rg-rotate" > "$ROOTFS/etc/rgos-device"
# Startup splash (video + sound, 720x720 for the square panel) and bundled launcher music. menu.py looks in /etc/rgos/splash and /opt/rgos/music;
# music / splash added to the ROMS partition (music/, splash/splash.mjpg) is picked up as well.
if [[ -d "$HERE/splash" ]]; then
  mkdir -p "$ROOTFS/etc/rgos/splash" && cp "$HERE"/splash/* "$ROOTFS/etc/rgos/splash/"
else
  echo "!! $HERE/splash missing: no startup splash in the image"
fi
if [[ -d "$HERE/music" ]]; then
  mkdir -p "$ROOTFS/opt/rgos/music" && cp "$HERE"/music/* "$ROOTFS/opt/rgos/music/"
else
  echo "!! $HERE/music missing: no bundled launcher music in the image"
fi
printf 'BUILD_ID=%s\n' "$(date +%Y%m%d-%H%M)" > "$ROOTFS/etc/rgos-release"
echo '{"enabled": false, "mountpoint": "/media/roms2"}' > "$ROOTFS/etc/rgos/sdcard.json"
# RetroArch config + the autoconfig profile for the input hub's pad
[[ -f "$HERE/config/retroarch.cfg" ]] || { echo "!! $HERE/config/retroarch.cfg missing"; exit 1; }
cp "$HERE/config/retroarch.cfg" "$ROOTFS/etc/rgos/retroarch.cfg"
mkdir -p "$ROOTFS/etc/rgos/autoconfig" && cp "$HERE"/config/autoconfig/*.cfg "$ROOTFS/etc/rgos/autoconfig/"
# Cores that have no Debian package: Neo Geo Pocket (mednafen_ngp, from NGP_SRC) and PlayStation (pcsx_rearmed, git clone with submodules
# from PSX_REPO). Prebuilt aarch64 *_libretro.so files in cores/ are copied as they are and those cores are then not built. Anything else is
# compiled inside the rootfs (native aarch64 under qemu, several minutes) and the compiler is removed again. A failed core build only costs
# that core (the build goes on and prints a "!!" line; PlayStation then has no core unless Debian offers libretro-beetle-psx).
LRDIR="$ROOTFS/usr/local/lib/libretro"; mkdir -p "$LRDIR"
# Beetle PSX (mednafen_psx) as a PlayStation fallback: not available in every Debian suite/component, so a miss is only a warning
cp -L /etc/resolv.conf "$ROOTFS/etc/resolv.conf" 2>/dev/null || true
if chroot "$ROOTFS" apt-get update >/dev/null 2>&1 \
   && chroot "$ROOTFS" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libretro-beetle-psx >/dev/null 2>&1; then
  echo "==> Installed libretro-beetle-psx (PlayStation fallback core)"
else
  echo "   (libretro-beetle-psx is not available in $DEBIAN_SUITE; PlayStation uses pcsx_rearmed only)"
fi
if ls "$HERE"/cores/*_libretro.so >/dev/null 2>&1; then
  echo "==> Copying prebuilt cores from cores/"; cp "$HERE"/cores/*_libretro.so "$LRDIR/"
fi
WANT_NGP=0; WANT_PSX=0
[[ "$NGP_SRC" != none && ! -e "$LRDIR/mednafen_ngp_libretro.so" ]] && WANT_NGP=1
[[ "$PSX_REPO" != none && ! -e "$LRDIR/pcsx_rearmed_libretro.so" ]] && WANT_PSX=1
if (( WANT_NGP || WANT_PSX )); then
  echo "==> Installing a compiler in the rootfs to build libretro cores"
  rm -rf "$ROOTFS/tmp/core-src"; mkdir -p "$ROOTFS/tmp/core-src"
  cp -L /etc/resolv.conf "$ROOTFS/etc/resolv.conf"
  if chroot "$ROOTFS" apt-get update && chroot "$ROOTFS" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gcc g++ make; then
    if (( WANT_NGP )); then
      echo "==> Building the Neo Geo Pocket core (mednafen_ngp)"
      if (
        set -e
        wget -q -O "$WORK/ngp-src.tar.gz" "$NGP_SRC"
        mkdir -p "$ROOTFS/tmp/core-src/ngp"; tar -xzf "$WORK/ngp-src.tar.gz" -C "$ROOTFS/tmp/core-src/ngp" --strip-components=1
        chroot "$ROOTFS" sh -c 'cd /tmp/core-src/ngp && make -j"$(nproc)"'
        install -m 755 "$ROOTFS"/tmp/core-src/ngp/mednafen_ngp_libretro.so "$LRDIR/mednafen_ngp_libretro.so"
      ); then echo "   mednafen_ngp_libretro.so built"
      else echo "!! could not build the Neo Geo Pocket core: NGP/NGPC will have no core (put a prebuilt mednafen_ngp_libretro.so in cores/ and rebuild)"; fi
    fi
    if (( WANT_PSX )); then
      echo "==> Building the PlayStation core (pcsx_rearmed, aarch64 dynarec)"
      if (
        set -e
        command -v git >/dev/null || { echo "git is not installed on this PC (apt install git)"; exit 1; }
        rm -rf "$WORK/pcsx_rearmed"
        git clone -q --depth 1 --recurse-submodules --shallow-submodules "$PSX_REPO" "$WORK/pcsx_rearmed"
        cp -r "$WORK/pcsx_rearmed" "$ROOTFS/tmp/core-src/psx"
        chroot "$ROOTFS" sh -c 'cd /tmp/core-src/psx && make -f Makefile.libretro -j"$(nproc)"'
        install -m 755 "$ROOTFS"/tmp/core-src/psx/pcsx_rearmed_libretro.so "$LRDIR/pcsx_rearmed_libretro.so"
      ); then echo "   pcsx_rearmed_libretro.so built"
      else echo "!! could not build pcsx_rearmed: PlayStation falls back to mednafen_psx (Beetle PSX, slower; software renderer)"; fi
    fi
  else
    echo "!! could not install a compiler in the rootfs: skipped building Neo Geo Pocket / PlayStation cores"
  fi
  chroot "$ROOTFS" env DEBIAN_FRONTEND=noninteractive apt-get purge -y --auto-remove gcc g++ make >/dev/null 2>&1 || true
  rm -rf "$ROOTFS/tmp/core-src" "$ROOTFS/etc/resolv.conf"
fi
CORES="$(ls "$ROOTFS"/usr/local/lib/libretro/*_libretro.so "$ROOTFS"/usr/lib/*/libretro/*_libretro.so "$ROOTFS"/usr/lib/libretro/*_libretro.so 2>/dev/null | xargs -rn1 basename | tr '\n' ' ' || true)"
echo "==> libretro cores in the image: ${CORES:-NONE}"
# a core counts as present if ANY of the library directories has it ("ls A B C" fails when just one of the globs is empty, which
# wrongly reported every core as missing)
core_present() { local f; for f in "$ROOTFS"/usr/lib/*/libretro/"$1"_libretro.so "$ROOTFS/usr/lib/libretro/$1"_libretro.so "$ROOTFS/usr/local/lib/libretro/$1"_libretro.so; do [[ -e "$f" ]] && return 0; done; return 1; }
for c in gambatte mgba nestopia snes9x mednafen_wswan mednafen_ngp pcsx_rearmed; do
  core_present "$c" || echo "!! ${c}_libretro.so is missing from the image (a Debian libretro package missing, or the source build of that core failed)"
done
# Beetle PSX is only an optional fallback for pcsx_rearmed
core_present mednafen_psx || core_present pcsx_rearmed || echo "!! no PlayStation core (neither pcsx_rearmed nor mednafen_psx) is in the image"
[[ -x "$ROOTFS/usr/bin/retroarch" ]] || echo "!! retroarch is not in the image (package missing from $DEBIAN_SUITE?)"
# applets the session calls that Debian minbase does not ship as commands
for a in reboot poweroff halt; do ln -sf /bin/busybox "$ROOTFS/sbin/$a"; done
ln -sf /usr/share/fonts/truetype/dejavu "$ROOTFS/usr/share/fonts/dejavu" 2>/dev/null || true

# PID 1. ROCKNIX's initramfs switch_roots into this SYSTEM and execs one of the paths below.
# It starts RG OS; if the session ever dies it falls back to a white screen so the device never panics.
cat > "$ROOTFS/usr/sbin/rgos-init" <<'INIT'
#!/bin/sh
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
mount -t proc proc /proc 2>/dev/null; mount -t sysfs sys /sys 2>/dev/null; mount -t devtmpfs dev /dev 2>/dev/null
[ -x /bin/busybox ] && /bin/busybox --install -s /usr/bin 2>/dev/null   # fills in only the commands that are missing

# --- the root filesystem is a read-only squashfs: give the session somewhere to write ------------------------------
mountpoint -q /run || mount -t tmpfs tmpfs /run
mountpoint -q /tmp || mount -t tmpfs tmpfs /tmp
mount -t tmpfs -o mode=755 tmpfs /var 2>/dev/null
mkdir -p /var/log /var/lib/rgos /var/tmp /var/cache /boot /media/roms
# The FAT partition (mmcblk*p1) was mounted READ-ONLY by ROCKNIX's init. Remount it read-write and expose it at /boot.
bootdev=""; for d in /dev/mmcblk*p1; do [ -b "$d" ] && { bootdev=$d; break; }; done
if [ -n "$bootdev" ] && ! mountpoint -q /boot; then
  mp=$(awk -v d="$bootdev" '$1==d {print $2; exit}' /proc/mounts)
  if [ -n "$mp" ]; then
    mount -o remount,rw "$mp" 2>/dev/null || mount -o remount,rw "$bootdev" 2>/dev/null
    mount --bind "$mp" /boot 2>/dev/null
  fi
  mountpoint -q /boot || mount -t vfat -o rw,noatime "$bootdev" /boot 2>/dev/null
fi
# settings (button map, wifi) persist on the FAT partition; otherwise they live in RAM
if mountpoint -q /boot && mkdir -p /boot/rgos-data 2>/dev/null && touch /boot/rgos-data/.w 2>/dev/null; then
  rm -f /boot/rgos-data/.w; mount --bind /boot/rgos-data /var/lib/rgos 2>/dev/null
fi
for i in 1 2 3 4 5 6 7 8 9 10; do [ -e /dev/fb0 ] && break; sleep 1; done
# display driver may be a module
[ -e /dev/fb0 ] || for pass in 1 2 3; do
  find /lib/modules -name '*.ko*' 2>/dev/null | while read -r m; do insmod "$m" 2>/dev/null; done
  [ -e /dev/fb0 ] && break
done
# boot facts for debugging, written to the FAT partition as rgos-bootinfo.txt
( sleep 6
  mkdir -p /run/bi
  for d in /dev/mmcblk*p1; do [ -b "$d" ] && mount -t vfat "$d" /run/bi 2>/dev/null && break; done
  mountpoint -q /run/bi || exit 0
  { echo "== uptime when written"; cat /proc/uptime; echo "== cmdline"; cat /proc/cmdline
    echo "== partitions"; cat /proc/partitions; echo "== blkid"; blkid; echo "== mounts"; cat /proc/mounts
    echo "== boot partition"; ls -la /run/bi; echo "== extlinux.conf"; cat /run/bi/extlinux/extlinux.conf
  } > /run/bi/rgos-bootinfo.txt 2>&1
  sync; umount /run/bi ) >/dev/null 2>&1 &
/opt/rgos/rgos-session
# only reached if the session script itself exited: show white and stay alive
for b in /sys/class/backlight/*; do [ -e "$b/max_brightness" ] && cat "$b/max_brightness" > "$b/brightness"; done
[ -e /dev/fb0 ] && tr '\0' '\377' < /dev/zero > /dev/fb0 2>/dev/null
while :; do sleep 3600; done
INIT
chmod +x "$ROOTFS/usr/sbin/rgos-init"
mkdir -p "$ROOTFS/usr/lib/systemd" "$ROOTFS/usr/lib/libreelec" "$ROOTFS/usr/lib/rocknix"
ln -sf ../../sbin/rgos-init "$ROOTFS/usr/lib/systemd/systemd"
ln -sf rgos-init            "$ROOTFS/usr/sbin/init"
ln -sf usr/sbin/rgos-init   "$ROOTFS/init"
rm -f "$ROOTFS/usr/bin/qemu-aarch64-static"
chroot "$ROOTFS" python3 -c 'import pygame' 2>&1 | tail -2 || true
rm -rf "$ROOTFS/var/cache/apt" "$ROOTFS/var/lib/apt/lists" "$ROOTFS/usr/share/doc" "$ROOTFS/usr/share/man"

# 4. new SYSTEM ------------------------------------------------------------------
echo "==> Building SYSTEM squashfs"
rm -f "$WORK/SYSTEM"
mksquashfs "$ROOTFS" "$WORK/SYSTEM" -comp "$COMP" -b "$BSIZE" -noappend -quiet
ls -la "$WORK/SYSTEM"

echo "==> Installing it on the boot partition"
FREE_KB="$(df -k --output=avail "$WORK/mnt" | tail -1)"
NEED_KB=$(( ( $(stat -c %s "$WORK/SYSTEM") - $(stat -c %s "$OLD_SYS") ) / 1024 + 1 + 4096 ))   # +4 MiB: rgos-initrd.cpio and extlinux.conf.stock
[[ $NEED_KB -lt $FREE_KB ]] || { echo "!! New SYSTEM doesn't fit on the boot partition"; exit 1; }
cp "$WORK/SYSTEM" "$WORK/mnt/SYSTEM"
if [[ -f "$WORK/mnt/SYSTEM.md5" ]]; then
  (cd "$WORK/mnt" && md5sum SYSTEM > SYSTEM.md5)
fi

# 5. replacement initramfs ---------------------------------------------------------
# ROCKNIX's own init (built into its kernel) is what shows "NEVER TOUCH boot=..." and waits 60 s when it is not happy with the
# card. Instead of fixing around it, the kernel is told to run ours: extlinux gets an INITRD line (our cpio) and rdinit=/rgos-init,
# so ROCKNIX's /init never runs. The kernel, DTB, bootloader area and partition table stay byte-for-byte as shipped.
echo "==> Building the RG OS initramfs (replaces ROCKNIX's init: no 60 s wait, no error screen)"
BB="$ROOTFS/bin/busybox"
[[ -x "$BB" ]] || { echo "!! $BB missing (busybox-static not installed in the rootfs?)"; exit 1; }
if readelf -lW "$BB" 2>/dev/null | grep -q INTERP; then echo "!! $BB is dynamically linked; the initramfs needs a static busybox"; exit 1; fi
INITRD_NAME="rgos-initrd.cpio"
python3 "$HERE/initramfs/mkinitramfs.py" "$BB" "$HERE/initramfs/init" "$WORK/mnt/$INITRD_NAME"
EXT="$WORK/mnt/extlinux/extlinux.conf"; [[ -f "$EXT" ]] || EXT="$WORK/mnt/extlinux.conf"
[[ -f "$EXT" ]] || { echo "!! no extlinux.conf on the boot partition"; exit 1; }
rm -f "$WORK/mnt/white.cpio.gz"                 # leftover from very old builds
sed -i 's/LABEL=ROCKNIX/LABEL=RGOS/g' "$EXT"       # the kernel command line names the boot partition by label
cp "$EXT" "$(dirname "$EXT")/extlinux.conf.stock"   # recovery: copy this over extlinux.conf from a PC to boot ROCKNIX's own init again
echo "==> Patched extlinux config:"
python3 "$HERE/initramfs/patch-extlinux.py" "$EXT" "/$INITRD_NAME"
grep -qi "^[[:space:]]*initrd[[:space:]]\+/$INITRD_NAME" "$EXT" && grep -q 'rdinit=/rgos-init' "$EXT" \
  || { echo "!! extlinux.conf was not patched correctly"; exit 1; }

# update mode: keep the files an offline update replaces (see rgos/rgos-update); the image itself is not needed
if [[ "$MODE" == "update" ]]; then
  rm -rf "$WORK/upd"; mkdir -p "$WORK/upd"
  cp "$WORK/mnt/SYSTEM" "$WORK/mnt/$INITRD_NAME" "$WORK/upd/"
  [[ -f "$WORK/mnt/SYSTEM.md5" ]] && cp "$WORK/mnt/SYSTEM.md5" "$WORK/upd/"
  # extlinux.conf is only included on request: a wrong one stops the card from booting, and the one on the card already
  # points at /rgos-initrd.cpio. UPDATE_EXTLINUX=1 sudo -E ./build.sh update
  [[ "${UPDATE_EXTLINUX:-0}" == "1" ]] && cp "$EXT" "$WORK/upd/extlinux.conf"
fi

sync
umount "$WORK/mnt"; losetup -d "$LOOP"; LOOP=""

if [[ "$MODE" == "update" ]]; then
  UPD_ID="${UPDATE_ID:-$(date +%Y%m%d-%H%M%S)}"
  UPD_TAR="$OUT/rgos-update-$UPD_ID.tar"
  ARGS=("SYSTEM=$WORK/upd/SYSTEM" "rgos-initrd.cpio=$WORK/upd/$INITRD_NAME")
  [[ -f "$WORK/upd/SYSTEM.md5" ]] && ARGS+=("SYSTEM.md5=$WORK/upd/SYSTEM.md5")
  [[ -f "$WORK/upd/extlinux.conf" ]] && ARGS+=("extlinux/extlinux.conf=$WORK/upd/extlinux.conf")
  rm -f "$OUT"/rgos-update-*.tar
  python3 "$HERE/tools/mkupdate.py" "$UPD_TAR" "$UPD_ID" "$(cat "$ROOTFS/etc/rgos-device")" "${ARGS[@]}"
  rm -rf "$WORK/upd"
  echo
  echo "Done: $UPD_TAR"
  echo "Copy it to the update/ folder of the ROMS partition (or the boot partition) and reboot the device; it installs it and restarts."
  exit 0
fi

# 6. partition table ---------------------------------------------------------------------------------------
# Partitions 1, 2 and 3 stay exactly as ROCKNIX shipped them. Partition 3 is the 2 MiB "uboot" partition: the SPL finds U-Boot on the
# card by that GPT name, and without it the device skips the card and boots Android. NEVER delete or rename it.
# The ROMS partition (4) is created by rgos/rgos-roms on the first boot, over all the free space, and formatted exFAT.
echo "==> Partitions in the image (partition 3 must be 'uboot'; ROMS is created on first boot as partition 4):"
sfdisk -l "$IMG" 2>/dev/null | grep -E '^(/|Device)' || true
sgdisk -c 1:RGOS "$IMG" >/dev/null 2>&1 || true   # GPT name of partition 1 (cosmetic)
sgdisk -i 3 "$IMG" 2>/dev/null | grep -q "^Partition name: 'uboot'" || { echo "!! partition 3 of the image is not 'uboot': the device would boot Android"; exit 1; }

echo
echo "Done: $IMG"
echo "To go back to ROCKNIX's own init: copy extlinux/extlinux.conf.stock over extlinux/extlinux.conf on the card's FAT partition."
echo "RG OS logs are copied to rgos-logs.txt on the card's FAT partition at 8, 28 and 58 s after boot."
echo "Flash the SPL once (cd spl-flasher && ./flash-spl.sh), then write the image to the SD card."
