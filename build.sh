#!/usr/bin/env bash
# rgos: Debian + RG OS launcher for Anbernic handhelds, built on top of the stock ROCKNIX SD image of each device.
#
#   rg-rotate   Anbernic RG Rotate  (Unisoc T618)   GPT, U-Boot found by the SPL through the "uboot" partition
#   rg34xxsp    Anbernic RG34XXSP   (Allwinner H700) MBR, U-Boot in front of partition 1 (stock ROCKNIX H700 image)
#   rg35xxh     Anbernic RG35XX H   (Allwinner H700) same stock ROCKNIX H700 image as the RG34XXSP, different dtb.img; 640x480 screen, two sticks
#
# For each device the stock ROCKNIX image is downloaded and ONLY its SYSTEM squashfs is replaced with a bare-bones Debian arm64 that
# starts the RG OS menu (white screen only as a fallback). The partition table, bootloader area, kernel and device tree(s) are left
# byte-for-byte alone (RG34XXSP: dtb.img is copied from the stock device_trees/ folder, exactly what the ROCKNIX install guide tells you to
# do by hand, and device_trees/ is then removed so dtb.img is the only device tree on the card). ROCKNIX's init (which waits 60 s and prints "NEVER TOUCH boot=...") is bypassed by a tiny RG OS initramfs (initramfs/)
# loaded through extlinux.conf; extlinux.conf.stock keeps the original.
#
# Run as root on Debian/Ubuntu (WSL2 is fine). A device is ALWAYS required; running it with no argument only prints this help:
#   ./build.sh rg34xxsp            SD card image for the RG34XXSP        -> out/rg34xxsp-rgos.img
#   ./build.sh rg35xxh             SD card image for the RG35XX H        -> out/rg35xxh-rgos.img
#   ./build.sh rg-rotate           SD card image for the RG Rotate       -> out/rg-rotate-rgos.img
#   ./build.sh all                 every device above, one after the other
#   ./build.sh <device|all> update offline update tar                    -> out/rgos-update-<device>-<id>.tar
#                                  (copy it to update/ on the ROMS partition and reboot)
# It re-runs itself through sudo when not root.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALL_DEVICES=(rg-rotate rg34xxsp rg35xxh)

usage() {
  cat <<EOF
usage: sudo ./build.sh <device> [image|update]

  device   rg34xxsp   Anbernic RG34XXSP (Allwinner H700, ROCKNIX-H700 base image)
           rg35xxh    Anbernic RG35XX H (Allwinner H700, ROCKNIX-H700 base image)
           rg-rotate  Anbernic RG Rotate (Unisoc T618, ROCKNIX-T618 base image)
           all        build every device above
  image    full SD card image (default)  -> out/<device>-rgos.img
  update   offline update tar            -> out/rgos-update-<device>-<id>.tar

A device is required: ./build.sh with no argument does nothing but print this text.
Options (environment): RG34XXSP: H700_RAM=DDR4|DDR3 (default DDR4, must match the console's RAM),
  RG34XXSP_DTB=sun50i-h700-anbernic-rg34xx-sp[-v2-panel], RG35XXH_DTB=sun50i-h700-anbernic-rg35xx-h[-rev6-panel], ROCKNIX_H700_VERSION=<release tag>; any device: ROCKNIX_URL=<image .img.gz>.
EOF
}

# ---- what to build: parsed (and, if it is wrong, rejected) BEFORE anything else happens: no sudo, no download, no file is touched
DEVICE_ARG="$(printf '%s' "${1:-}" | tr 'A-Z_' 'a-z-')"
MODE="${2:-image}"; [[ "$MODE" == "build" ]] && MODE=image      # "build" is accepted as an alias for "image"
case "$DEVICE_ARG" in
  rg34xxsp|rg34xx-sp)  DEVICE_ARG=rg34xxsp ;;
  rg35xxh|rg35xx-h)    DEVICE_ARG=rg35xxh ;;
  rg-rotate|rgrotate)  DEVICE_ARG=rg-rotate ;;
  all)                 ;;
  "")                  usage; exit 1 ;;
  *)                   echo "unknown device '${1}'" >&2; echo >&2; usage >&2; exit 1 ;;
esac
[[ "$MODE" == "image" || "$MODE" == "update" ]] || { echo "unknown mode '${2}' (use image or update)" >&2; echo >&2; usage >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || { echo "ERROR: this build needs root; run it as root or install sudo" >&2; exit 1; }
  exec sudo -E bash "${BASH_SOURCE[0]}" "$DEVICE_ARG" "$MODE"
fi

# ---- all: run this script once per device (separate work directories, same update id), then report
if [[ "$DEVICE_ARG" == "all" ]]; then
  export UPDATE_ID="${UPDATE_ID:-$(date +%Y%m%d-%H%M%S)}"
  failed=()
  for d in "${ALL_DEVICES[@]}"; do
    echo; echo "################ building $d ($MODE) ################"
    bash "${BASH_SOURCE[0]}" "$d" "$MODE" || failed+=("$d")
  done
  echo
  if (( ${#failed[@]} )); then echo "!! build FAILED for: ${failed[*]}" >&2; exit 1; fi
  echo "All devices built: ${ALL_DEVICES[*]} (output in $HERE/out)"
  exit 0
fi
DEVICE="$DEVICE_ARG"

# ---- device profile -------------------------------------------------------------------------------------------------------------
#   PTABLE       partition table of the stock image: gpt | dos
#   BASE_URL     default stock ROCKNIX image (override: ROCKNIX_URL)
case "$DEVICE" in
  rg-rotate)
    DEVICE_DESC="Anbernic RG Rotate (Unisoc T618)"
    PTABLE=gpt
    BASE_URL="https://github.com/beebono/distribution/releases/download/beta-20260818/ROCKNIX-T618.aarch64-20260818.img.gz"
    ;;
  rg34xxsp)
    DEVICE_DESC="Anbernic RG34XXSP (Allwinner H700)"
    PTABLE=dos
    # The H700 ROCKNIX release ships one image per RAM type (the U-Boot/SPL in front of partition 1 differs); both contain the device
    # trees of every H700 console. The RG34XXSP is a DDR4 (LPDDR4) console; use H700_RAM=DDR3 only if your unit really has DDR3.
    H700_RAM="${H700_RAM:-DDR4}"
    [[ "$H700_RAM" == "DDR4" || "$H700_RAM" == "DDR3" ]] || { echo "H700_RAM must be DDR4 or DDR3" >&2; exit 1; }
    ROCKNIX_H700_VERSION="${ROCKNIX_H700_VERSION:-20261001}"
    BASE_URL="https://github.com/ROCKNIX/distribution/releases/download/${ROCKNIX_H700_VERSION}/ROCKNIX-H700.aarch64-${ROCKNIX_H700_VERSION}-${H700_RAM}.img.gz"
    # ROCKNIX boots the device tree named dtb.img in the root of the boot partition (extlinux.conf: FDT /dtb.img). The install guide has you
    # copy device_trees/<your console>.dtb to dtb.img by hand; the build does exactly that and then deletes device_trees/. v2 panel units: RG34XXSP_DTB=sun50i-h700-anbernic-rg34xx-sp-v2-panel
    RG34XXSP_DTB="${RG34XXSP_DTB:-sun50i-h700-anbernic-rg34xx-sp}"
    [[ "$RG34XXSP_DTB" =~ ^sun50i-h700-anbernic-rg34xx-sp(-v2-panel)?$ ]] || { echo "RG34XXSP_DTB must be sun50i-h700-anbernic-rg34xx-sp or sun50i-h700-anbernic-rg34xx-sp-v2-panel" >&2; exit 1; }
    H700_DTB="$RG34XXSP_DTB"
    ;;
  rg35xxh)
    DEVICE_DESC="Anbernic RG35XX H (Allwinner H700)"
    PTABLE=dos
    # Same stock ROCKNIX H700 image as the RG34XXSP (it carries every H700 console's device tree). The RG35XX H has 1 GiB LPDDR4 (linux-sunxi),
    # so DDR4 is the default; H700_RAM=DDR3 only if your unit really needs it. Two PCB generations exist and the panel differs: if the
    # screen shows garbage with the default dtb, rebuild with RG35XXH_DTB=sun50i-h700-anbernic-rg35xx-h-rev6-panel (ROCKNIX install guide).
    H700_RAM="${H700_RAM:-DDR4}"
    [[ "$H700_RAM" == "DDR4" || "$H700_RAM" == "DDR3" ]] || { echo "H700_RAM must be DDR4 or DDR3" >&2; exit 1; }
    ROCKNIX_H700_VERSION="${ROCKNIX_H700_VERSION:-20261001}"
    BASE_URL="https://github.com/ROCKNIX/distribution/releases/download/${ROCKNIX_H700_VERSION}/ROCKNIX-H700.aarch64-${ROCKNIX_H700_VERSION}-${H700_RAM}.img.gz"
    RG35XXH_DTB="${RG35XXH_DTB:-sun50i-h700-anbernic-rg35xx-h}"
    [[ "$RG35XXH_DTB" =~ ^sun50i-h700-anbernic-rg35xx-h(-rev6-panel)?$ ]] || { echo "RG35XXH_DTB must be sun50i-h700-anbernic-rg35xx-h or sun50i-h700-anbernic-rg35xx-h-rev6-panel" >&2; exit 1; }
    H700_DTB="$RG35XXH_DTB"
    ;;
esac
echo "==> Device: $DEVICE_DESC  (mode: $MODE)"

ROCKNIX_URL="${ROCKNIX_URL:-$BASE_URL}"
DEBIAN_SUITE="${DEBIAN_SUITE:-trixie}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://deb.debian.org/debian}"
PSX_REPO="${PSX_REPO:-https://github.com/libretro/pcsx_rearmed.git}"   # PlayStation core (pcsx_rearmed, built from source); PSX_REPO=none skips it
GENESIS_REPO="${GENESIS_REPO:-https://github.com/libretro/Genesis-Plus-GX.git}"   # Genesis Plus GX (Mega Drive / Master System / Game Gear / SG-1000 / Sega CD), built from source only if Debian has no package; GENESIS_REPO=none skips that fallback
GC_REPO="${GC_REPO:-https://github.com/libretro/dolphin.git}"   # Nintendo GameCube core (Dolphin libretro, built from source). RG Rotate ONLY (other devices never get it); GC_REPO=none skips it
[[ "$DEVICE" == "rg-rotate" ]] || GC_REPO=none
NGP_SRC="${NGP_SRC:-https://github.com/libretro/beetle-ngp-libretro/archive/refs/heads/master.tar.gz}"   # Neo Geo Pocket core (mednafen_ngp); NGP_SRC=none skips it
# Emulation: RetroArch (aarch64) + Gambatte (Game Boy / Game Boy Color) + mGBA (Game Boy Advance, also a GB/GBC alternative),
# with Mesa (Panfrost) for KMS output and SDL2 for input.
# Also: Nestopia (NES / Famicom, Debian package libretro-nestopia), Snes9x (SNES; Debian non-free, hence --components below) and Beetle WonderSwan (WS / WSC). libretro-beetle-psx is NOT in EMU_PKGS:
# a package missing from the suite aborts debootstrap, so it is installed best-effort later (PlayStation uses pcsx_rearmed, built from source). The Neo Geo Pocket core has no Debian
# package: it is compiled from source in the rootfs below (NGP_SRC), or drop a prebuilt aarch64 *_libretro.so into cores/ next to build.sh.
# Genesis Plus GX is installed best-effort after debootstrap (Debian package libretro-genesisplusgx, else built from GENESIS_REPO), because a package missing from the suite would abort debootstrap.
# More libretro cores: EXTRA_PKGS="libretro-xyz" sudo -E ./build.sh  (Debian package names)
EMU_PKGS="retroarch,libretro-gambatte,libretro-mgba,libretro-snes9x,libretro-nestopia,libretro-beetle-wswan,libretro-core-info,libgl1-mesa-dri,libegl1,libegl-mesa0,libgles2,libgbm1,libsdl2-2.0-0"
EXTRA_PKGS="${EXTRA_PKGS:-}"
# The exFAT ROMS partition (partition 4) is NOT in the image: rgos/rgos-roms creates it on the device's first boot, using all the
# free space on the card, and formats it exFAT.

WORK="$HERE/work/$DEVICE"; OUT="$HERE/out"; IMG="$OUT/$DEVICE-rgos.img"
[[ "$MODE" == "update" ]] && IMG="$WORK/update-scratch.img"   # an update build never overwrites a finished SD card image
ROOTFS="$WORK/rootfs"

need=(debootstrap losetup mksquashfs unsquashfs wget gunzip md5sum python3 readelf sfdisk truncate fatlabel)
[[ "$MODE" == "image" && "$PTABLE" == gpt ]] && need+=(sgdisk)
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
  [[ -f "$RX_GZ" ]] || { echo "==> Downloading the stock ROCKNIX image for $DEVICE"; echo "    $ROCKNIX_URL"
    wget -O "$RX_GZ" "$ROCKNIX_URL" || { rm -f "$RX_GZ"; echo "!! download failed. Set ROCKNIX_URL=<url of the stock image .img.gz> (RG34XXSP / RG35XX H: or ROCKNIX_H700_VERSION=<release tag>, H700_RAM=DDR4|DDR3), or put the .img.gz into $WORK/ and re-run." >&2; exit 1; }; }
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

# H700: boot the right device tree. dtb.img is what extlinux.conf (FDT /dtb.img) loads; device_trees/ holds one dtb per console.
if [[ "$DEVICE" == "rg34xxsp" || "$DEVICE" == "rg35xxh" ]]; then
  DTB_SRC="$WORK/mnt/device_trees/$H700_DTB.dtb"
  [[ -f "$DTB_SRC" ]] || { echo "!! $H700_DTB.dtb is not in the stock image's device_trees/ ($(ls "$WORK/mnt/device_trees" 2>/dev/null | tr '\n' ' '))"; exit 1; }
  echo "==> dtb.img <- device_trees/$H700_DTB.dtb"
  cp -f "$DTB_SRC" "$WORK/mnt/dtb.img"
  # only this console's dtb.img stays on the RGOS partition: the stock device_trees/ folder (one dtb per H700 console) is removed
  echo "==> Removing device_trees/ from the boot partition (dtb.img is the only device tree kept)"
  rm -rf "$WORK/mnt/device_trees"
  grep -qi '^[[:space:]]*fdt[[:space:]]\+/dtb.img' "$WORK/mnt/extlinux/extlinux.conf" 2>/dev/null \
    || echo "!! stock extlinux.conf does not load /dtb.img (FDT line differs); check the config printed above"
fi

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
# Everything debootstrap installed (python3, python3-pygame and the SDL libraries it needs, ...) is marked manual, so the later
# "apt-get purge --auto-remove" of the compiler toolchain can never remove it as an orphaned dependency.
chroot "$ROOTFS" sh -c "dpkg-query -W -f='\${Package}\n' | xargs -r apt-mark manual" >/dev/null 2>&1 || true
echo "$DEVICE-rgos" > "$ROOTFS/etc/hostname"

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
# Audio: the T618 codec's speaker routes are vendor mixer controls that stay off after boot. Take the mixer state ROCKNIX saves
# (asound.state) and any asound.conf from the stock SYSTEM; rgos-audio-prep restores it at boot. init.txt (audio/) adds commands.
mkdir -p "$ROOTFS/etc/rgos/audio"
AST="$(find "$WORK/system" -name asound.state -type f 2>/dev/null | head -1)"
if [[ -n "$AST" ]]; then cp "$AST" "$ROOTFS/etc/rgos/audio/asound.state"; echo "==> Stock mixer state: $AST"
else echo "!! no asound.state in the stock SYSTEM: the speaker relies on audio/init.txt and the driver defaults"; fi
ACF="$(find "$WORK/system" -name asound.conf -type f 2>/dev/null | head -1)"; [[ -n "$ACF" ]] && cp "$ACF" "$ROOTFS/etc/asound.conf" && echo "==> Stock asound.conf: $ACF"
echo "==> Stock audio scripts (for reference): $(find "$WORK/system" -type f \( -iname '*audio*' -o -iname '*amixer*' -o -iname '*alsa*.sh' \) -path '*bin*' 2>/dev/null | sed "s#$WORK/system/##" | head -10 | tr '\n' ' ')"
[[ -d "$HERE/audio" ]] && cp "$HERE"/audio/init.txt "$ROOTFS/etc/rgos/audio/init.txt"
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
for f in usr/sbin/dropbear usr/bin/dropbearkey usr/lib/openssh/sftp-server usr/bin/bluetoothctl usr/bin/dbus-daemon sbin/mkfs.ext4 sbin/mkfs.exfat usr/sbin/sgdisk sbin/sfdisk usr/sbin/partx sbin/wpa_supplicant; do
  [[ -e "$ROOTFS/$f" || -e "$ROOTFS/usr/$f" ]] || echo "!! $f is missing from the image"
done
ls "$ROOTFS"/usr/libexec/bluetooth/bluetoothd "$ROOTFS"/usr/lib/bluetooth/bluetoothd >/dev/null 2>&1 || echo "!! bluetoothd is missing from the image"
echo "$DEVICE" > "$ROOTFS/etc/rgos-device"
# RG34XXSP only: analog sticks. The input hub publishes ABS_X/Y/RX/RY of the stock joypad device (input-rg34xxsp.json, read by rgos-input
# because it is named after /etc/rgos-device) and RetroArch gets the matching axis binds (retroarch-rg34xxsp.cfg, appended by rgos-launch).
# Other devices get neither file, so their input is exactly what it was.
if [[ "$DEVICE" == "rg34xxsp" || "$DEVICE" == "rg35xxh" ]]; then
  mkdir -p "$ROOTFS/etc/rgos"
  echo '{"analog": true}' > "$ROOTFS/etc/rgos/input-$DEVICE.json"
  # the RG35XX H shares the RG34XXSP's axis binds (same ROCKNIX H700 joypad: left = axes 0/1, right = 2/3)
  RA_CFG="$HERE/config/retroarch-$DEVICE.cfg"; [[ -f "$RA_CFG" ]] || RA_CFG="$HERE/config/retroarch-rg34xxsp.cfg"
  [[ -f "$RA_CFG" ]] || { echo "!! $RA_CFG missing"; exit 1; }
  cp "$RA_CFG" "$ROOTFS/etc/rgos/retroarch-$DEVICE.cfg"
fi
# Startup splash (video + sound, sized for the device's canvas: splash-<device>.mjpg) and bundled launcher music. menu.py looks in /etc/rgos/splash and /opt/rgos/music;
# music / splash added to the ROMS partition (music/, splash/splash.mjpg) is picked up as well.
if [[ -d "$HERE/splash" ]]; then
  mkdir -p "$ROOTFS/etc/rgos/splash"
  for f in "$HERE"/splash/*; do
    case "$(basename "$f")" in splash-*) [[ "$(basename "$f")" == splash-"$DEVICE".* ]] || continue ;; esac   # only this device's video
    cp "$f" "$ROOTFS/etc/rgos/splash/"
  done
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
# Genesis Plus GX (Mega Drive / Genesis, Master System, Game Gear, SG-1000, Sega CD): Debian package if the suite has it, else built below
if chroot "$ROOTFS" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libretro-genesisplusgx >/dev/null 2>&1; then
  echo "==> Installed libretro-genesisplusgx (Genesis Plus GX)"
else
  echo "   (libretro-genesisplusgx is not available in $DEBIAN_SUITE; it will be built from $GENESIS_REPO)"
fi
if ls "$HERE"/cores/*_libretro.so >/dev/null 2>&1; then
  echo "==> Copying prebuilt cores from cores/"; cp "$HERE"/cores/*_libretro.so "$LRDIR/"
fi
[[ "$DEVICE" == "rg-rotate" ]] || rm -f "$LRDIR/dolphin_libretro.so"   # GameCube core: RG Rotate only, even if a prebuilt one sits in cores/
WANT_NGP=0; WANT_PSX=0; WANT_GEN=0; WANT_GC=0
[[ "$NGP_SRC" != none && ! -e "$LRDIR/mednafen_ngp_libretro.so" ]] && WANT_NGP=1
[[ "$PSX_REPO" != none && ! -e "$LRDIR/pcsx_rearmed_libretro.so" ]] && WANT_PSX=1
# (core_present is defined further down; this is the same test, needed before the compiler step)
have_core() { local f; for f in "$ROOTFS"/usr/lib/*/libretro/"$1"_libretro.so "$ROOTFS/usr/lib/libretro/$1"_libretro.so "$ROOTFS/usr/local/lib/libretro/$1"_libretro.so; do [[ -e "$f" ]] && return 0; done; return 1; }
[[ "$GENESIS_REPO" != none ]] && ! have_core genesis_plus_gx && WANT_GEN=1
[[ "$GC_REPO" != none && ! -e "$LRDIR/dolphin_libretro.so" ]] && WANT_GC=1
if (( WANT_NGP || WANT_PSX || WANT_GEN || WANT_GC )); then
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
    if (( WANT_GEN )); then
      echo "==> Building the Genesis Plus GX core (genesis_plus_gx)"
      if (
        set -e
        command -v git >/dev/null || { echo "git is not installed on this PC (apt install git)"; exit 1; }
        rm -rf "$WORK/genesis-plus-gx"
        git clone -q --depth 1 "$GENESIS_REPO" "$WORK/genesis-plus-gx"
        cp -r "$WORK/genesis-plus-gx" "$ROOTFS/tmp/core-src/gen"
        chroot "$ROOTFS" sh -c 'cd /tmp/core-src/gen && make -f Makefile.libretro platform=unix -j"$(nproc)"'
        install -m 755 "$ROOTFS"/tmp/core-src/gen/genesis_plus_gx_libretro.so "$LRDIR/genesis_plus_gx_libretro.so"
      ); then echo "   genesis_plus_gx_libretro.so built"
      else echo "!! could not build Genesis Plus GX: Mega Drive / Master System / Game Gear / Sega CD will have no core (put a prebuilt genesis_plus_gx_libretro.so in cores/ and rebuild)"; fi
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
    if (( WANT_GC )); then
      echo "==> Building the GameCube core (Dolphin libretro, GameCube only in the menu; RG Rotate)"
      if chroot "$ROOTFS" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git ca-certificates cmake ninja-build pkg-config \
           libevdev-dev libudev-dev libasound2-dev libpulse-dev libgl-dev libegl-dev libgles-dev libdrm-dev libgbm-dev libusb-1.0-0-dev libbluetooth-dev \
           libcurl4-openssl-dev libpng-dev libminizip-dev liblzo2-dev libmbedtls-dev libhidapi-dev zlib1g-dev libxi-dev libxrandr-dev libx11-dev \
           && (
        rm -rf "$WORK/dolphin-libretro"
        git clone -q --depth 1 --recurse-submodules --shallow-submodules "$GC_REPO" "$WORK/dolphin-libretro" || exit 1
        cp -r "$WORK/dolphin-libretro" "$ROOTFS/tmp/core-src/gc" || exit 1
        chroot "$ROOTFS" sh -c 'cd /tmp/core-src/gc && cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DLIBRETRO=ON -DENABLE_QT=OFF -DENABLE_NOGUI=OFF -DENABLE_X11=OFF -DENABLE_TESTS=OFF -DENABLE_ANALYTICS=OFF -DENABLE_LTO=OFF -DUSE_DISCORD_PRESENCE=OFF -DENABLE_CLI_TOOL=OFF && cmake --build build -j"$(nproc)"' || exit 1
        so="$(find "$ROOTFS/tmp/core-src/gc" -name dolphin_libretro.so -type f | head -n1)"
        [[ -n "$so" ]] || exit 1
        install -m 755 "$so" "$LRDIR/dolphin_libretro.so" || exit 1
        # Dolphin looks for <system dir>/dolphin-emu/Sys; rgos-launch copies this to <ROMS>/bios/dolphin-emu/Sys on the first GameCube launch
        mkdir -p "$ROOTFS/usr/local/share/rgos/dolphin-emu"
        cp -r "$ROOTFS/tmp/core-src/gc/Data/Sys" "$ROOTFS/usr/local/share/rgos/dolphin-emu/Sys" || exit 1
        # keep the runtime libraries the core links against when the -dev packages are purged below
        cat > "$ROOTFS/tmp/core-src/keep-libs.sh" <<'KEEP'
ldd /usr/local/lib/libretro/dolphin_libretro.so | awk '/=>/ {print $3}' | xargs -r dpkg -S 2>/dev/null | cut -d: -f1 | sort -u | xargs -r apt-mark manual
KEEP
        chroot "$ROOTFS" sh /tmp/core-src/keep-libs.sh
      ); then echo "   dolphin_libretro.so built"
      else echo "!! could not build the GameCube core: GameCube will not appear on the RG Rotate (put a prebuilt dolphin_libretro.so in cores/ and rebuild)"; rm -f "$LRDIR/dolphin_libretro.so"; fi
    fi
  else
    echo "!! could not install a compiler in the rootfs: skipped building the Genesis Plus GX / Neo Geo Pocket / PlayStation cores"
  fi
  chroot "$ROOTFS" env DEBIAN_FRONTEND=noninteractive apt-get purge -y --auto-remove gcc g++ make git cmake ninja-build pkg-config '*-dev' >/dev/null 2>&1 || true
  # the launcher cannot start without pygame: if the purge (or anything else) took it away, put it back before the network config goes
  if ! chroot "$ROOTFS" python3 -c 'import pygame' >/dev/null 2>&1; then
    echo "!! pygame is not importable after the toolchain purge; reinstalling python3-pygame"
    chroot "$ROOTFS" apt-get update >/dev/null 2>&1 || true
    chroot "$ROOTFS" env DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall python3-pygame || true
  fi
  rm -rf "$ROOTFS/tmp/core-src" "$ROOTFS/etc/resolv.conf"
fi
CORES="$(ls "$ROOTFS"/usr/local/lib/libretro/*_libretro.so "$ROOTFS"/usr/lib/*/libretro/*_libretro.so "$ROOTFS"/usr/lib/libretro/*_libretro.so 2>/dev/null | xargs -rn1 basename | tr '\n' ' ' || true)"
echo "==> libretro cores in the image: ${CORES:-NONE}"
# a core counts as present if ANY of the library directories has it ("ls A B C" fails when just one of the globs is empty, which
# wrongly reported every core as missing)
core_present() { local f; for f in "$ROOTFS"/usr/lib/*/libretro/"$1"_libretro.so "$ROOTFS/usr/lib/libretro/$1"_libretro.so "$ROOTFS/usr/local/lib/libretro/$1"_libretro.so; do [[ -e "$f" ]] && return 0; done; return 1; }
for c in gambatte mgba nestopia genesis_plus_gx snes9x mednafen_wswan mednafen_ngp pcsx_rearmed; do
  core_present "$c" || echo "!! ${c}_libretro.so is missing from the image (a Debian libretro package missing, or the source build of that core failed)"
done
if [[ "$DEVICE" == "rg-rotate" && "$GC_REPO" != none ]]; then core_present dolphin || echo "!! dolphin_libretro.so is missing from the image: no GameCube on this build"; fi
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
if ! chroot "$ROOTFS" python3 -c 'import pygame; print("pygame", pygame.version.ver)'; then
  echo "==> pygame missing from the rootfs: installing python3-pygame"
  cp -L /etc/resolv.conf "$ROOTFS/etc/resolv.conf" 2>/dev/null || true
  chroot "$ROOTFS" apt-get update || true
  chroot "$ROOTFS" env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pygame || true
  rm -f "$ROOTFS/etc/resolv.conf"
  chroot "$ROOTFS" python3 -c 'import pygame; print("pygame", pygame.version.ver)' \
    || { echo "!! pygame still cannot be imported: the RG OS launcher would not start. Aborting the build." >&2; exit 1; }
fi
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
  # RG34XXSP: dtb.img is only included on request (UPDATE_DTB=1): the card may carry a hand-picked one (v2 panel) that must survive updates
  [[ ( "$DEVICE" == "rg34xxsp" || "$DEVICE" == "rg35xxh" ) && "${UPDATE_DTB:-0}" == "1" ]] && cp "$WORK/mnt/dtb.img" "$WORK/upd/dtb.img"
fi

sync
umount "$WORK/mnt"; losetup -d "$LOOP"; LOOP=""

if [[ "$MODE" == "update" ]]; then
  UPD_ID="${UPDATE_ID:-$(date +%Y%m%d-%H%M%S)}"
  UPD_TAR="$OUT/rgos-update-$DEVICE-$UPD_ID.tar"
  ARGS=("SYSTEM=$WORK/upd/SYSTEM" "rgos-initrd.cpio=$WORK/upd/$INITRD_NAME")
  [[ -f "$WORK/upd/SYSTEM.md5" ]] && ARGS+=("SYSTEM.md5=$WORK/upd/SYSTEM.md5")
  [[ -f "$WORK/upd/extlinux.conf" ]] && ARGS+=("extlinux/extlinux.conf=$WORK/upd/extlinux.conf")
  [[ -f "$WORK/upd/dtb.img" ]] && ARGS+=("dtb.img=$WORK/upd/dtb.img")
  rm -f "$OUT"/rgos-update-"$DEVICE"-*.tar
  python3 "$HERE/tools/mkupdate.py" "$UPD_TAR" "$UPD_ID" "$(cat "$ROOTFS/etc/rgos-device")" "${ARGS[@]}"
  rm -rf "$WORK/upd"
  echo
  echo "Done: $UPD_TAR"
  echo "Copy it to the update/ folder of the ROMS partition (or the boot partition) and reboot the device; it installs it and restarts."
  exit 0
fi

# 6. partition table ---------------------------------------------------------------------------------------
# Partitions stay exactly as ROCKNIX shipped them; the ROMS partition is created by rgos/rgos-roms on the first boot, over all the free
# space of the card, and formatted exFAT.
if [[ "$PTABLE" == gpt ]]; then
  # GPT (RG Rotate): partition 3 is the 2 MiB "uboot" partition: the SPL finds U-Boot on the card by that GPT name, and without it the
  # device skips the card and boots Android. NEVER delete or rename it. ROMS becomes partition 4.
  echo "==> Partitions in the image (partition 3 must be 'uboot'; ROMS is created on first boot as partition 4):"
  sfdisk -l "$IMG" 2>/dev/null | grep -E '^(/|Device)' || true
  sgdisk -c 1:RGOS "$IMG" >/dev/null 2>&1 || true   # GPT name of partition 1 (cosmetic)
  sgdisk -i 3 "$IMG" 2>/dev/null | grep -q "^Partition name: 'uboot'" || { echo "!! partition 3 of the image is not 'uboot': the device would boot Android"; exit 1; }
else
  # MBR (RG34XXSP): partitions 1 (FAT) and 2 (STORAGE). U-Boot (SPL at 8 KiB) sits in the gap before partition 1, which was not touched
  # (the image is a byte copy of the stock one). ROMS is added as partition 3 on first boot.
  echo "==> Partitions in the image (MBR; ROMS is created on first boot as partition 3):"
  sfdisk -l "$IMG" 2>/dev/null | grep -E '^(/|Device|Disklabel)' || true
  sfdisk -d "$IMG" 2>/dev/null | grep -q '^label: dos' || { echo "!! the stock H700 image is not an MBR (dos) image: rgos-roms would not know how to add ROMS"; exit 1; }
  [[ "$(sfdisk -d "$IMG" 2>/dev/null | grep -c "^$IMG[0-9]* :")" -le 3 ]] || { echo "!! the image already has 4 partitions: no slot left for ROMS"; exit 1; }
fi

echo
echo "Done: $IMG"
echo "To go back to ROCKNIX's own init: copy extlinux/extlinux.conf.stock over extlinux/extlinux.conf on the card's FAT partition."
echo "RG OS logs are copied to rgos-logs.txt on the card's FAT partition at 8, 28 and 58 s after boot."
if [[ "$DEVICE" == "rg-rotate" ]]; then
  echo "Flash the SPL once (cd spl-flasher && ./flash-spl.sh), then write the image to the SD card."
else
  echo "Write the image to the SD card (the H700 boots straight from it; no SPL flashing). dtb.img = $H700_DTB.dtb; to change it, rebuild with RG34XXSP_DTB=... / RG35XXH_DTB=... (device_trees/ is not on the card)."
fi
