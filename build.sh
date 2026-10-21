#!/usr/bin/env bash
# RG OS image builder for supported Anbernic H700 handhelds: ROCKNIX boot files + Debian root + RG OS menu.
# Run on an x86-64 or arm64 Debian/Ubuntu host with internet access, as a normal user with sudo.
# Usage: ./build all                 (build images for every supported device)
#        ./build.sh all               (same as ./build all)
#        ./build.sh rg34xxsp [stage]  (default device; stage defaults to all)
#        ./build.sh rg35xxsp [stage]  (Anbernic RG35XX SP; stage defaults to all)
#        ./build.sh tfa|uboot|kernel|rootfs|image (single stage for the default RG34XX-SP)
#        ./build.sh update          re-apply overlay/ to the existing rootfs and build ONLY out/rgos-update-<id>.tar
# ./build.sh (image stage) also writes out/rgos-update-<BUILD_ID>.tar: copy it into the "update" folder on the
# ROMS (exFAT) partition and reboot the device to install it (kernel, initramfs, DTB and the whole root filesystem;
# ROMS, saves and states are never touched). BUILD_ID=<text> overrides the default timestamp id.
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$PWD

# "all" is a device selector: build each supported device independently so their cached
# root filesystems, device trees, and output images never overlap. An optional stage is
# forwarded to both builds (for example, ./build all image).
if [ "${1:-}" = all ]; then
  shift
  STAGE=${1:-all}
  case "$STAGE" in
    all|tfa|uboot|kernel|rootfs|image|update) ;;
    *) echo "unknown stage for all-device build: $STAGE"; echo "usage: ./build all [all|tfa|uboot|kernel|rootfs|image|update]"; exit 1 ;;
  esac
  for device in rg34xxsp rg35xxsp; do
    echo
    echo "========== RG OS build: $device (stage: $STAGE) =========="
    # Clear path overrides to preserve each device's separate default work/output directories.
    env -u WORK -u OUT "$ROOT/build.sh" "$device" "$STAGE"
  done
  exit 0
fi

# Select a device before choosing per-device work/output paths. This prevents a cached rootfs or DTB from
# one handheld accidentally being packaged for the other.
case "${1:-}" in
  rg34xxsp|rg34xx-sp) shift; DEVICE=rg34xx-sp; [ $# -gt 0 ] || set -- all ;;
  rg35xxsp|rg35xx-sp) shift; DEVICE=rg35xx-sp; [ $# -gt 0 ] || set -- all ;;
  *) DEVICE=rg34xx-sp ;;
esac
export DEVICE
if [ "$DEVICE" = rg35xx-sp ]; then
  WORK_DEFAULT=$ROOT/work-rg35xxsp; OUT_DEFAULT=$ROOT/out-rg35xxsp
else
  WORK_DEFAULT=$ROOT/work; OUT_DEFAULT=$ROOT/out
fi
WORK=${WORK:-$WORK_DEFAULT}; OUT=${OUT:-$OUT_DEFAULT}
TFA_TAG=${TFA_TAG:-v2.12.0}
UBOOT_TAG=${UBOOT_TAG:-v2025.07}
KERNEL_TAG=${KERNEL_TAG:-v6.15}
# Optional prebuilt boot files (skip building them). The supplied DTB is ROCKNIX-style and needs a
# kernel that matches it (DE33 display mixer, rocknix joypad, vendor audio) -- stock mainline cannot drive it.
#   KERNEL_IMAGE=/path/to/Image          arm64 kernel Image matching the DTB
#   KERNEL_MODULES=/path/to/lib/modules  (optional) directory containing <kernel-version>/ modules
#   UBOOT_BIN=/path/to/u-boot-sunxi-with-spl.bin   (optional) known-good U-Boot for this device
KERNEL_IMAGE=${KERNEL_IMAGE:-}; KERNEL_MODULES=${KERNEL_MODULES:-}; UBOOT_BIN=${UBOOT_BIN:-}
# Boot source. Default "rocknix": the build downloads the official ROCKNIX H700 image and takes its
# U-Boot (raw, at 8 KiB) and its kernel, because the supplied DTB only works with that kernel.
#   BOOT_SRC=rocknix|mainline   (mainline = old behaviour: build TF-A/U-Boot/Linux v6.15; DTB will NOT match)
#   ROCKNIX_DDR=4|3             RAM type of your unit; 4 = DDR4 image (default). If nothing boots, try 3.
#   ROCKNIX_URL=...             exact .img.gz URL (skips the GitHub "latest release" lookup)
BOOT_SRC=${BOOT_SRC:-rocknix}; ROCKNIX_DDR=${ROCKNIX_DDR:-4}; ROCKNIX_URL=${ROCKNIX_URL:-}
# Use the shipped RG34XX-SP DTB; for RG35XX-SP, extract its matching DTB from the official ROCKNIX H700
# boot partition so it matches the kernel being reused. RGOS_DTB=/path/to/dtb overrides either.
ROCKNIX_DIR=${ROCKNIX_DIR:-$WORK/rocknix}
if [ "$DEVICE" = rg35xx-sp ]; then
  RGOS_DTB=${RGOS_DTB:-$ROCKNIX_DIR/sun50i-h700-anbernic-rg35xx-sp.dtb}
else
  RGOS_DTB=${RGOS_DTB:-$ROOT/dtb/sun50i-h700-anbernic-rg34xx-sp.dtb}
fi
UBOOT_DEFCONFIG=${UBOOT_DEFCONFIG:-anbernic_rg35xx_h700_defconfig}
SUITE=${SUITE:-trixie}
#   ROMS_FS=auto|exfat|vfat   filesystem for the ROMS partition made at first boot. auto = exFAT when the running kernel has it.
ROMS_FS=${ROMS_FS:-auto}; ROMS_FSTAB=exfat
# Partition sizes. The image contains ONLY the boot (FAT32) and root (ext4) partitions. The exFAT ROMS partition
# is not part of the image: on first boot the device creates it in all the space after the root partition and
# formats it (see overlay/usr/local/sbin/rgos-firstboot). The ext4 root is sized to the built rootfs plus headroom.
#   ROOT_MB=auto|<n>        root size in MiB (default auto = rootfs size + ROOT_HEADROOM_MB)
#   ROOT_HEADROOM_MB=96     free space left on / for logs, saved state, etc.
#   IMG_MB=auto|<n>         total image size (default auto = 16 + BOOT_MB + ROOT_MB)
IMG_MB=${IMG_MB:-auto}
BOOT_MB=${BOOT_MB:-128}; ROOT_MB=${ROOT_MB:-auto}; ROOT_HEADROOM_MB=${ROOT_HEADROOM_MB:-96}
J=$(nproc)
[ "$(uname -m)" = aarch64 ] && CROSS= || CROSS=aarch64-linux-gnu-
mkdir -p "$WORK" "$OUT"

# mkfs.ext4 for the root partition (drops the orphan_file feature when this mkfs knows it, for older kernels).
mkext4() { local o=; if mkfs.ext4 -V >/dev/null 2>&1 && truncate -s 8M "$WORK/.e4probe" && mkfs.ext4 -q -F -O ^orphan_file "$WORK/.e4probe" >/dev/null 2>&1; then o="-O ^orphan_file"; fi; rm -f "$WORK/.e4probe"; sudo mkfs.ext4 $o "$@"; }
need() { for c in "$@"; do command -v "$c" >/dev/null || { echo "missing: $c"; exit 1; }; done; }
clone() { [ -d "$2" ] || git clone --depth 1 --branch "$3" "$1" "$2"; }

# Make sure $ROCKNIX_DIR/rocknix.img.gz exists (download the newest ROCKNIX H700 image of the chosen DDR type unless ROCKNIX_URL pins one).
rocknix_download() {
  local R="$ROCKNIX_DIR" url
  mkdir -p "$R"
  if [ ! -f "$R/rocknix.img.gz" ]; then
    url=$ROCKNIX_URL
    if [ -z "$url" ]; then
      url=$(curl -fsSL https://api.github.com/repos/ROCKNIX/distribution/releases/latest | python3 -c '
import sys, json
ddr = sys.argv[1]
for a in json.load(sys.stdin).get("assets", []):
    n = a["name"]
    if "H700" in n and n.endswith("-DDR%s.img.gz" % ddr):
        print(a["browser_download_url"]); break
' "$ROCKNIX_DDR") || true
    fi
    [ -n "$url" ] || { echo "could not find a ROCKNIX H700 DDR$ROCKNIX_DDR image; set ROCKNIX_URL=<.img.gz url>"; exit 1; }
    echo "downloading $url"
    curl -fL --retry 3 -o "$R/rocknix.img.gz.part" "$url" && mv "$R/rocknix.img.gz.part" "$R/rocknix.img.gz"
  fi
}

rocknix_fetch() {
  local R="$ROCKNIX_DIR" img START LOOP K
  [ -f "$R/uboot.bin" ] && [ -f "$R/KERNEL" ] && [ -f "$R/modules.done" ] && [ -f "$RGOS_DTB" ] && return 0
  need curl gzip losetup sfdisk python3 dd file
  mkdir -p "$R/mnt"
  rocknix_download
  img="$R/rocknix.img"
  [ -f "$img" ] || gzip -dc "$R/rocknix.img.gz" > "$img"
  # U-Boot = everything between 8 KiB and the start of the first partition
  START=$(sfdisk -d "$img" | sed -n 's/.*start= *\([0-9]\{1,\}\),.*/\1/p' | sort -n | head -1)
  [ -n "$START" ] && [ "$START" -gt 16 ] || { echo "could not read ROCKNIX partition table"; exit 1; }
  dd if="$img" of="$R/uboot.bin" bs=512 skip=16 count=$((START-16)) status=none
  LOOP=$(sudo losetup -rfP --show "$img")
  trap 'sudo umount "$R/mnt" 2>/dev/null; sudo losetup -d "$LOOP" 2>/dev/null' EXIT
  sudo mount -o ro "${LOOP}p1" "$R/mnt" || { echo "cannot mount ROCKNIX partition 1"; exit 1; }
  K=$(sudo find "$R/mnt" -maxdepth 1 -type f \( -name KERNEL -o -name Image -o -name 'Image.*' \) | head -1)
  [ -n "$K" ] || { echo "no kernel found on ROCKNIX p1; contents:"; sudo ls -la "$R/mnt"; exit 1; }
  sudo cp "$K" "$R/KERNEL"
  # The RG35XX-SP has its own panel/button wiring. Never reuse the RG34XX-SP DTB for it.
  if [ "$DEVICE" = rg35xx-sp ] && [ ! -f "$RGOS_DTB" ]; then
    DTB_NAME=sun50i-h700-anbernic-rg35xx-sp.dtb
    DTB=$(sudo find "$R/mnt" -type f \( -path "*/device_trees/$DTB_NAME" -o -name "$DTB_NAME" \) | head -1)
    [ -n "$DTB" ] || { echo "missing $DTB_NAME in ROCKNIX H700 image; cannot safely build for RG35XX-SP"; exit 1; }
    mkdir -p "$(dirname "$RGOS_DTB")"
    sudo cp "$DTB" "$RGOS_DTB"
    sudo chown "$(id -u):$(id -g)" "$RGOS_DTB"
    echo "using RG35XX-SP DTB: $RGOS_DTB"
  fi
  # ROCKNIX keeps its kernel modules (joypad, ADC, PWM, audio, ...) inside the SYSTEM squashfs, NOT in the
  # kernel image. Without them the buttons never appear, so pull them out and ship them in the rootfs.
  need unsquashfs
  rm -rf "$R/modules" "$R/sq"; mkdir -p "$R/modules"
  if [ -f "$R/mnt/SYSTEM" ]; then
    # find every <...>/lib/modules/<kernel-version> directory anywhere in the image (LibreELEC-style images
    # keep them under usr/lib/kernel-overlays/..., so don't assume a fixed path)
    sudo unsquashfs -l "$R/mnt/SYSTEM" 2>/dev/null | sed 's#^squashfs-root/##' > "$R/system.list" || true
    VERDIRS=$(grep -E '(^|/)lib/modules/[^/]+/.*\.ko' "$R/system.list" \
              | sed -E 's#(.*lib/modules/[^/]+)/.*#\1#' | sort -u)
    if [ -n "$VERDIRS" ]; then
      for vd in $VERDIRS; do
        sudo unsquashfs -n -f -d "$R/sq" "$R/mnt/SYSTEM" "$vd" >/dev/null 2>&1 || true
        [ -d "$R/sq/$vd" ] && sudo cp -a "$R/sq/$vd" "$R/modules/"
      done
      sudo chown -R "$(id -u):$(id -g)" "$R/modules"
      echo "ROCKNIX kernel modules: $(ls "$R/modules" | tr '\n' ' ') ($(du -sm "$R/modules" | cut -f1) MB, $(find "$R/modules" -name '*.ko*' | wc -l) files)"
      find "$R/modules" -name '*.ko*' | grep -iE 'joypad|adc|pwm' | sed 's#.*/#  driver: #'
    else
      echo "WARNING: no kernel modules found inside ROCKNIX SYSTEM; buttons may not work. 'modules'-like paths:"
      grep -i 'modules' "$R/system.list" | head -20
    fi
    sudo rm -rf "$R/sq"
  else
    echo "WARNING: no SYSTEM file on ROCKNIX p1; cannot extract kernel modules"
  fi
  touch "$R/modules.done"
  [ -f "$R/mnt/extlinux/extlinux.conf" ] && sudo cp "$R/mnt/extlinux/extlinux.conf" "$R/extlinux.conf" || true
  echo "--- ROCKNIX p1 contents:"; sudo ls -la "$R/mnt"
  sudo chown "$(id -u):$(id -g)" "$R/KERNEL" "$R/extlinux.conf" 2>/dev/null || true
  sudo umount "$R/mnt"; sudo losetup -d "$LOOP"; trap - EXIT
  file "$R/KERNEL"
  rm -f "$img"   # free the disk; U-Boot and kernel are already extracted
}

tfa() {
  [ -z "$UBOOT_BIN" ] || { echo "using prebuilt U-Boot, skipping TF-A"; return 0; }
  [ "$BOOT_SRC" != rocknix ] || { echo "BOOT_SRC=rocknix: using ROCKNIX U-Boot, skipping TF-A"; return 0; }
  clone https://github.com/ARM-software/arm-trusted-firmware "$WORK/tfa" "$TFA_TAG"
  make -C "$WORK/tfa" -j"$J" CROSS_COMPILE=$CROSS PLAT=sun50i_h616 DEBUG=1 bl31
}
uboot() {
  if [ -n "$UBOOT_BIN" ]; then cp "$UBOOT_BIN" "$OUT/u-boot-sunxi-with-spl.bin"; return 0; fi
  if [ "$BOOT_SRC" = rocknix ]; then rocknix_fetch; cp "$ROCKNIX_DIR/uboot.bin" "$OUT/u-boot-sunxi-with-spl.bin"; return 0; fi
  clone https://source.denx.de/u-boot/u-boot.git "$WORK/uboot" "$UBOOT_TAG"
  cd "$WORK/uboot"
  make CROSS_COMPILE=$CROSS "$UBOOT_DEFCONFIG"
  make -j"$J" CROSS_COMPILE=$CROSS BL31="$WORK/tfa/build/sun50i_h616/debug/bl31.bin"
  cp u-boot-sunxi-with-spl.bin "$OUT/"; cd "$ROOT"
}
kernel() {
  if [ -n "$KERNEL_IMAGE" ]; then
    [ -f "$KERNEL_IMAGE" ] || { echo "KERNEL_IMAGE not found: $KERNEL_IMAGE"; exit 1; }
    cp "$KERNEL_IMAGE" "$OUT/Image"
    rm -rf "$WORK/modules"; mkdir -p "$WORK/modules/lib/modules"
    [ -z "$KERNEL_MODULES" ] || cp -a "$KERNEL_MODULES"/. "$WORK/modules/lib/modules/"
    echo "using prebuilt kernel $KERNEL_IMAGE"; return 0
  fi
  if [ "$BOOT_SRC" = rocknix ]; then
    rocknix_fetch; cp "$ROCKNIX_DIR/KERNEL" "$OUT/Image"
    rm -rf "$WORK/modules"; mkdir -p "$WORK/modules/lib/modules"
    cp -a "$ROCKNIX_DIR/modules/." "$WORK/modules/lib/modules/" 2>/dev/null || true
    return 0
  fi
  clone https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git "$WORK/linux" "$KERNEL_TAG"
  cd "$WORK/linux"
  make ARCH=arm64 CROSS_COMPILE=$CROSS defconfig
  for s in EXT4_FS VFAT_FS EXFAT_FS MMC_SUNXI INPUT_JOYSTICK JOYSTICK_ADC KEYBOARD_GPIO \
           INPUT_EVDEV DRM_SUN4I DRM_SUN8I_MIXER DRM_PANFROST DRM_PANEL_SITRONIX_ST7701 \
           DRM_PANEL_NEWVISION_NV3052C MFD_AXP20X_I2C REGULATOR_AXP20X SND_SUN4I_I2S \
           SND_SOC_SUN8I_CODEC SND_SOC_SUN50I_CODEC_ANALOG BACKLIGHT_PWM; do
    scripts/config --enable "$s"
  done
  make ARCH=arm64 CROSS_COMPILE=$CROSS olddefconfig
  # No "dtbs" target: the kernel's own DTBs are not used (see RGOS_DTB above).
  make ARCH=arm64 CROSS_COMPILE=$CROSS -j"$J" Image modules
  cp arch/arm64/boot/Image "$OUT/Image"
  rm -rf "$WORK/modules"; make ARCH=arm64 INSTALL_MOD_PATH="$WORK/modules" modules_install
  cd "$ROOT"
}
apply_overlay() {
  sudo cp -a overlay/. "$WORK/rootfs/"
  sudo sed -i 's#^/dev/mmcblk0p3 /media/roms#LABEL=ROMS      /media/roms#' "$WORK/rootfs/etc/fstab" 2>/dev/null || true   # ROMS is found by label (kernel numbers p3..p5 = env/boot/env-redund)
  echo "$DEVICE" | sudo tee "$WORK/rootfs/etc/rgos-device" >/dev/null   # tools/rgos-update refuses updates built for another device
  sudo chmod 755 "$WORK/rootfs/usr/local/sbin/rgos-firstboot" "$WORK/rootfs/opt/rgos/rgos-session" \
                 "$WORK/rootfs/opt/rgos/menu.py" "$WORK/rootfs/opt/rgos/rgos-volume" \
                 "$WORK/rootfs/opt/rgos/rgos-portkey" "$WORK/rootfs/opt/rgos/rgos-raguard" "$WORK/rootfs/opt/rgos/rgos-sticks" \
                 "$WORK/rootfs/opt/rgos/rgos-perf" "$WORK/rootfs/opt/rgos/rgos-videoprobe" \
                 "$WORK/rootfs/opt/rgos/rgos-hw" "$WORK/rootfs/opt/rgos/rgos-hwprobe" "$WORK/rootfs/opt/rgos/rgos-poweroff" \
                 "$WORK/rootfs/opt/rgos/rgos-inputprobe" "$WORK/rootfs/opt/rgos/rgos-wifi" "$WORK/rootfs/opt/rgos/rgos-wifi-dhcp" \
                 "$WORK/rootfs/opt/rgos/rgos-sdcard" "$WORK/rootfs/opt/rgos/sdcard.py"
  # enable the volume-button service here (not only in rootfs()) so `./build.sh update` picks it up too
  sudo mkdir -p "$WORK/rootfs/etc/systemd/system/multi-user.target.wants"
  sudo ln -sf /etc/systemd/system/rgos-volume.service "$WORK/rootfs/etc/systemd/system/multi-user.target.wants/rgos-volume.service"
  sudo ln -sf /etc/systemd/system/rgos-sticks.service "$WORK/rootfs/etc/systemd/system/multi-user.target.wants/rgos-sticks.service"
  # second SD card: mounts a 2nd card on /media/roms2 (udev rule 91-rgos-sdcard.rules re-runs it on hot-plug); does nothing with one card
  sudo ln -sf /etc/systemd/system/rgos-sdcard.service "$WORK/rootfs/etc/systemd/system/multi-user.target.wants/rgos-sdcard.service"
  # Wi-Fi daemon: idles ("no-adapter") when the kernel has no wireless interface, so enabling it everywhere is harmless
  sudo ln -sf /etc/systemd/system/rgos-wifi.service "$WORK/rootfs/etc/systemd/system/multi-user.target.wants/rgos-wifi.service"
  # rgos-hw exits at once on devices without /etc/rgos/hw-<device>.json, so enabling it everywhere changes nothing on the RG34XX-SP
  sudo ln -sf /etc/systemd/system/rgos-hw.service "$WORK/rootfs/etc/systemd/system/multi-user.target.wants/rgos-hw.service"
  sudo rm -f "$WORK/rootfs/etc/systemd/logind.conf.d/rgos-power.conf"
}
rootfs() {
  need debootstrap
  [ -f /usr/share/keyrings/debian-archive-keyring.gpg ] || {
    echo "missing Debian keyring: run  sudo apt install debian-archive-keyring  and re-run ./build.sh rootfs"; exit 1; }
  # --- preflight: the rootfs must live on a real Linux filesystem with room to unpack ---
  mkdir -p "$WORK"
  local fstype opts avail_mb
  fstype=$(df -PT "$WORK" | awk 'NR==2{print $2}')
  opts=$(findmnt -n -T "$WORK" -o OPTIONS 2>/dev/null || true)
  case "$fstype" in
    ntfs*|fuseblk|vfat|exfat|msdos|9p|drvfs|fuse.*|cifs|nfs*)
      echo "WORK=$WORK is on '$fstype', which cannot hold a Linux root filesystem (permissions/device nodes)."
      echo "Use a native Linux folder, e.g.:  WORK=\$HOME/rgos-work ./build.sh rootfs"; exit 1 ;;
  esac
  case ",$opts," in
    *,nodev,*|*,noexec,*) echo "WORK=$WORK is mounted with nodev/noexec ($opts); debootstrap cannot work there."
                          echo "Use another folder, e.g.:  WORK=\$HOME/rgos-work ./build.sh rootfs"; exit 1 ;;
  esac
  avail_mb=$(df -Pm "$WORK" | awk 'NR==2{print $4}')
  if [ "$avail_mb" -lt 4000 ]; then
    echo "only ${avail_mb} MB free on $WORK's filesystem; the rootfs build needs about 4 GB."
    echo "Free some space (e.g. rm -f $ROCKNIX_DIR/rocknix.img.gz after the kernel stage) or use WORK=/other/path"; exit 1
  fi
  sudo rm -rf "$WORK/rootfs"
  # non-free-firmware is for firmware-realtek (Wi-Fi)
  sudo debootstrap --arch=arm64 --variant=minbase --components=main,non-free-firmware \
    --include=systemd-sysv,udev,kmod,util-linux,fdisk,exfatprogs,dosfstools,alsa-utils,python3,python3-pygame,unzip,xz-utils,curl,wget,jq,wpasupplicant,iw,iproute2,wireless-regdb,firmware-realtek,systemd-timesyncd,libopenal1,libsdl2-image-2.0-0,libsdl2-mixer-2.0-0,libsdl2-ttf-2.0-0,libsdl2-net-2.0-0,fonts-dejavu-core,ca-certificates,libgl1-mesa-dri,libegl1,libgles2,libgbm1,libdrm2,procps,nano,busybox-static,retroarch,libretro-mgba,libretro-core-info \
    "$SUITE" "$WORK/rootfs" http://deb.debian.org/debian || {
      echo; echo "debootstrap FAILED. Last lines of its log ($WORK/rootfs/debootstrap/debootstrap.log):"
      sudo tail -n 30 "$WORK/rootfs/debootstrap/debootstrap.log" 2>/dev/null
      echo; echo "disk space:"; df -h "$WORK"; exit 1; }
  # Gambatte provides Game Boy and Game Boy Color emulation through one libretro core.
  GAMBATTE_ZIP="$WORK/gambatte_libretro.so.zip"
  curl -fL --retry 3 --retry-delay 2 \
    "https://buildbot.libretro.com/nightly/linux/aarch64/latest/gambatte_libretro.so.zip" \
    -o "$GAMBATTE_ZIP" || { echo "Failed to download the official ARM64 Gambatte core"; exit 1; }
  sudo install -d "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro"
  sudo unzip -o "$GAMBATTE_ZIP" -d "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro" >/dev/null || { echo "Failed to extract Gambatte core"; exit 1; }
  sudo test -s "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro/gambatte_libretro.so" || { echo "Downloaded archive did not contain gambatte_libretro.so"; exit 1; }
  rm -f "$GAMBATTE_ZIP"
  # Debian Trixie does not package libretro-fceumm; fetch the official prebuilt ARM64 core.
  FCEUMM_ZIP="$WORK/fceumm_libretro.so.zip"
  curl -fL --retry 3 --retry-delay 2 \
    "https://buildbot.libretro.com/nightly/linux/aarch64/latest/fceumm_libretro.so.zip" \
    -o "$FCEUMM_ZIP" || { echo "Failed to download the prebuilt ARM64 FCEUmm core"; exit 1; }
  sudo install -d "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro"
  sudo unzip -o "$FCEUMM_ZIP" -d "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro" >/dev/null || { echo "Failed to extract FCEUmm core"; exit 1; }
  sudo test -s "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro/fceumm_libretro.so" || { echo "Downloaded archive did not contain fceumm_libretro.so"; exit 1; }
  rm -f "$FCEUMM_ZIP"
  # Genesis Plus GX provides the Sega Mega Drive / Genesis libretro core on ARM64.
  GENESIS_ZIP="$WORK/genesis_plus_gx_libretro.so.zip"
  curl -fL --retry 3 --retry-delay 2 \
    "https://buildbot.libretro.com/nightly/linux/aarch64/latest/genesis_plus_gx_libretro.so.zip" \
    -o "$GENESIS_ZIP" || { echo "Failed to download the official ARM64 Genesis Plus GX core"; exit 1; }
  sudo unzip -o "$GENESIS_ZIP" -d "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro" >/dev/null || { echo "Failed to extract Genesis Plus GX core"; exit 1; }
  sudo test -s "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro/genesis_plus_gx_libretro.so" || { echo "Downloaded archive did not contain genesis_plus_gx_libretro.so"; exit 1; }
  rm -f "$GENESIS_ZIP"
  # Snes9x provides Super Nintendo / Super Famicom emulation on ARM64.
  SNES9X_ZIP="$WORK/snes9x_libretro.so.zip"
  curl -fL --retry 3 --retry-delay 2 \
    "https://buildbot.libretro.com/nightly/linux/aarch64/latest/snes9x_libretro.so.zip" \
    -o "$SNES9X_ZIP" || { echo "Failed to download the official ARM64 Snes9x core"; exit 1; }
  sudo unzip -o "$SNES9X_ZIP" -d "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro" >/dev/null || { echo "Failed to extract Snes9x core"; exit 1; }
  sudo test -s "$WORK/rootfs/usr/lib/aarch64-linux-gnu/libretro/snes9x_libretro.so" || { echo "Downloaded archive did not contain snes9x_libretro.so"; exit 1; }
  rm -f "$SNES9X_ZIP"
  apply_overlay
  sudo cp -a "$WORK/modules/lib/modules" "$WORK/rootfs/lib/"
  # make sure module dependency/alias maps exist so udev auto-loads the joypad/ADC/PWM drivers at boot
  for v in "$WORK/rootfs/lib/modules"/*/; do
    [ -d "$v" ] && sudo depmod -b "$WORK/rootfs" "$(basename "$v")" || true
  done
  # slim down: strip module debug info, drop caches, headers
  sudo find "$WORK/rootfs/lib/modules" -name '*.ko' -exec ${CROSS}strip --strip-debug {} +
  sudo rm -rf "$WORK/rootfs/usr/include" "$WORK/rootfs/usr/src" \
              "$WORK/rootfs/usr/share/doc" "$WORK/rootfs/usr/share/man" "$WORK/rootfs/usr/share/info" \
              "$WORK/rootfs/var/cache/apt" "$WORK/rootfs/var/lib/apt/lists"
  sudo rm -rf "$WORK/rootfs/lib/modules"/*/build "$WORK/rootfs/lib/modules"/*/source
  # more slimming: translations, test suites, completions, license texts, python bytecode caches
  sudo find "$WORK/rootfs/usr/share/locale" -mindepth 1 -maxdepth 1 ! -name 'en*' -exec rm -rf {} + 2>/dev/null || true
  sudo rm -rf "$WORK/rootfs/usr/share/bash-completion" "$WORK/rootfs/usr/share/lintian" \
              "$WORK/rootfs/usr/share/common-licenses" "$WORK/rootfs/usr/lib/python3/dist-packages/pygame/tests" \
              "$WORK/rootfs/usr/lib/python3/dist-packages/pygame/docs" "$WORK/rootfs/usr/lib/python3/dist-packages/pygame/examples" \
              "$WORK/rootfs/var/cache/debconf" "$WORK/rootfs/var/log"/*
  sudo find "$WORK/rootfs/usr" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
  echo rgos | sudo tee "$WORK/rootfs/etc/hostname" >/dev/null
  sudo tee "$WORK/rootfs/etc/fstab" >/dev/null <<FSTAB
/dev/mmcblk0p1 /boot       vfat  defaults,noatime            0 2
/dev/mmcblk0p2 /           ext4  defaults,noatime            0 1
LABEL=ROMS      /media/roms $ROMS_FSTAB defaults,noatime,nofail,x-systemd.device-timeout=3s,umask=000 0 0
FSTAB
  sudo mkdir -p "$WORK/rootfs/media/roms" "$WORK/rootfs/boot" "$WORK/rootfs/etc/rgos"
  echo "$ROMS_FS" | sudo tee "$WORK/rootfs/etc/rgos/romsfs" >/dev/null
  W="$WORK/rootfs/etc/systemd/system/multi-user.target.wants"; sudo mkdir -p "$W"
  sudo ln -sf /etc/systemd/system/rgos.service "$W/rgos.service"
  sudo ln -sf /etc/systemd/system/rgos-firstboot.service "$W/rgos-firstboot.service"
  sudo ln -sf /etc/systemd/system/rgos-volume.service "$W/rgos-volume.service"
  sudo ln -sf /dev/null "$WORK/rootfs/etc/systemd/system/getty@tty1.service"   # RG OS owns tty1
  sudo sed -i 's/^root:[^:]*:/root::/' "$WORK/rootfs/etc/shadow"                # empty root pw (dev)
}
# Tiny initramfs that mounts the Debian root and switch_roots. It is passed as an external initrd,
# so it overrides /init of any initramfs baked into the ROCKNIX kernel (external is unpacked on top).
# Before switching root it also applies a firmware update from <ROMS>/update/*.tar (see tools/rgos-update).
initrd() {
  need cpio gzip
  local d="$WORK/initrd" m f; rm -rf "$d"; mkdir -p "$d"/{bin,proc,sys,dev,newroot,boot,roms,tmp,lib/modules}
  cp "$WORK/rootfs/bin/busybox" "$d/bin/busybox"
  [ -f "$ROOT/tools/rgos-update" ] || { echo "missing $ROOT/tools/rgos-update"; exit 1; }
  cp "$ROOT/tools/rgos-update" "$d/bin/rgos-update"; chmod 755 "$d/bin/rgos-update"
  # vfat/exfat (+ helpers) are needed in the initramfs to read the update. Only copied when the kernel builds them
  # as MODULES (nothing is found, and nothing is loaded, if they are built in).
  : > "$d/lib/modules/order"
  for m in crc16 mbcache jbd2 ext4 nls_utf8 nls_cp437 nls_iso8859-1 fat vfat exfat; do
    f=$(find "$WORK/modules/lib/modules" \( -name "$m.ko" -o -name "$m.ko.*" \) 2>/dev/null | head -n 1 || true)
    [ -n "$f" ] || continue
    case "$f" in
      *.ko)     cp "$f" "$d/lib/modules/$m.ko" ;;
      *.ko.xz)  xz -dc "$f" > "$d/lib/modules/$m.ko" ;;
      *.ko.zst) zstd -dc "$f" > "$d/lib/modules/$m.ko" ;;
      *.ko.gz)  gzip -dc "$f" > "$d/lib/modules/$m.ko" ;;
      *)        continue ;;
    esac
    echo "$m" >> "$d/lib/modules/order"
  done
  echo "initramfs filesystem modules: $(tr '\n' ' ' < "$d/lib/modules/order")(none = built into the kernel)"
  cat > "$d/init" <<'INIT'
#!/bin/busybox sh
/bin/busybox --install -s /bin
export PATH=/bin:/sbin
mount -t proc proc /proc; mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null
# The A133 BSP kernel arms a 16 s hardware watchdog (sunxi-wdt, "Watchdog enabled (timeout=16 sec, nowayout=0)") and nothing in
# our userspace feeds it, so the board reset ~19 s after power-on. Magic close ('V') switches it off.
for w in /dev/watchdog /dev/watchdog0; do [ -c "$w" ] && { exec 8>"$w"; echo V >&8; exec 8>&-; break; }; done 2>/dev/null
# make sure our messages reach the screen (initramfs may start with no stdio)
exec >/dev/console 2>&1 </dev/console
echo 7 > /proc/sys/kernel/printk
echo "RGOS-INIT: started, waiting for SD root partition..."
load_mods() {
  [ -s /lib/modules/order ] || return 0
  for m in $(cat /lib/modules/order); do insmod "/lib/modules/$m.ko" 2>/dev/null; done
}
load_mods          # ext4/vfat may be modules on the vendor kernel: load them before the first mount
# status colours on the framebuffer (no PC needed): $1 = grey level (octal) for the whole screen, or "split" = top half white, bottom dark
fbstat() {
  [ -c /dev/fb0 ] || return 0
  if [ "$1" = split ]; then
    { dd if=/dev/zero bs=65536 count=28 2>/dev/null | tr '\000' '\377'; dd if=/dev/zero bs=65536 count=40 2>/dev/null | tr '\000' '\020'; } > /dev/fb0 2>/dev/null
  else
    # $1 = octal byte value (e.g. 377 = white). tr needs a real escape: passing "300" filled the screen with the character '3'.
    dd if=/dev/zero bs=1M count=8 2>/dev/null | tr '\000' "\\$1" > /dev/fb0 2>/dev/null
  fi
}

# --- boot beacon: prove the kernel + display path are alive even if nothing later works. Backlight to a high level and the
# framebuffer filled with grey (a fully black screen after this means the display driver never came up). Best effort only.
mount -t debugfs none /sys/kernel/debug 2>/dev/null
for b in /sys/class/backlight/*; do
  [ -w "$b/brightness" ] && cat "$b/max_brightness" > "$b/brightness" 2>/dev/null
done
DD=/sys/kernel/debug/dispdbg
if [ -w $DD/command ]; then
  echo lcd0 > $DD/name; echo setbl > $DD/command; echo 200 > $DD/param; echo 1 > $DD/start
fi 2>/dev/null
[ -c /dev/fb0 ] && { dd if=/dev/zero bs=1M count=8 2>/dev/null | tr '\000' '\100' > /dev/fb0; } 2>/dev/null

# --- diagnostics written to the FAT boot partition (read rgos-boot.txt on a PC; works with a dark screen)
save_diag() {
  for p in /dev/mmcblk*p1; do
    [ -b "$p" ] || continue
    mount -t vfat -o rw "$p" /boot 2>/dev/null || continue
    {
      echo "== rgos initramfs diag ($1)"; echo "== uname"; uname -a; echo "== cmdline"; cat /proc/cmdline
      echo "== partitions"; cat /proc/partitions; echo "== filesystems"; cat /proc/filesystems
      echo "== mount errors"; cat /tmp/mounterr 2>&1; echo "== graphics"; ls -l /sys/class/graphics /sys/class/backlight /dev/fb* /dev/dri 2>&1
      for f in name virtual_size bits_per_pixel; do echo "fb0 $f: $(cat /sys/class/graphics/fb0/$f 2>&1)"; done
      echo "== dmesg"; dmesg
    } > /boot/rgos-boot.txt 2>&1
    sync; umount /boot; break
  done
}

# the root is ours if it has an init, or (card left half-updated) if it carries the rgos-root label
is_root() {
  [ -x /newroot/sbin/init ] || [ -L /newroot/sbin/init ] && return 0
  [ "$(dd if="$1" bs=1 skip=1144 count=16 2>/dev/null | tr -d '\000')" = rgos-root ]
}
# apply <ROMS>/update/*.tar if there is one. $1 = the root partition (p2); p1/p3 are on the same card.
try_update() {
  base=${1%p2}
  [ -b "${base}p1" ] || return 0
  grep -qw exfat /proc/filesystems || load_mods
  # ROMS is whichever later partition mounts and has an update/ folder 
  ok=
  for q in 3 4 5 6 7 8 9; do
    [ -b "${base}p$q" ] || continue
    mount -t exfat -o rw,noatime "${base}p$q" /roms 2>/dev/null || mount -t vfat -o rw,noatime "${base}p$q" /roms 2>/dev/null || continue
    if [ -d /roms/update ]; then ok=1; break; fi
    umount /roms
  done
  [ -n "$ok" ] || return 0
  if ls /roms/update/*.tar >/dev/null 2>&1; then
    grep -qw vfat /proc/filesystems || load_mods
    if mount -t vfat -o rw,noatime "${base}p1" /boot 2>/dev/null; then
      NEWROOT=/newroot ROMS=/roms BOOT=/boot /bin/rgos-update
      # raw Android-image partition = the 20 MiB one (40960 sectors); kernel partition numbers are compact, so do not guess p5
      if [ -s /boot/boot.img ]; then
        for q in 3 4 5 6 7; do
          [ "$(cat /sys/class/block/$(basename ${base})p$q/size 2>/dev/null)" = 40960 ] && dd if=/boot/boot.img of="${base}p$q" bs=1M conv=fsync 2>/dev/null
        done
      fi
      sync; umount /boot
    else
      echo "RGOS-UPDATE: cannot mount the boot partition - update skipped"
    fi
  fi
  sync; umount /roms
}
i=0; while [ $i -lt 15 ] && ! ls /dev/mmcblk*p1 >/dev/null 2>&1; do sleep 1; i=$((i+1)); done
save_diag early           # written before the root search, so a hang below still leaves a log on the card
found=
i=0
while [ -z "$found" ] && [ $i -lt 40 ]; do
  for d in /dev/mmcblk0p2 /dev/mmcblk1p2; do
    [ -b "$d" ] || continue
    if mount -t ext4 -o rw "$d" /newroot 2>>/tmp/mounterr; then
      if is_root "$d"; then found=$d; break; fi
      umount /newroot
    fi
  done
  [ -n "$found" ] || { sleep 1; i=$((i+1)); }
done
sleep 1; save_diag "root=${found:-none}"
# Root found -> wipe the screen BLACK before systemd takes over (this used to be WHITE, and the white/grey fills stayed visible in
# patches around the boot-log text). Root NOT found -> top white / bottom dark; dark grey = never got this far.
[ -n "$found" ] && fbstat 000 || fbstat split
if [ -n "$found" ]; then
  try_update "$found"
  if [ -x /newroot/sbin/init ] || [ -L /newroot/sbin/init ]; then
    echo "RGOS-INIT: root is $found, switching root"
    mount --move /proc /newroot/proc; mount --move /sys /newroot/sys; mount --move /dev /newroot/dev
    exec switch_root /newroot /sbin/init
  fi
  echo "RGOS-INIT: $found has no /sbin/init (update interrupted? the update file is retried on next boot)"
fi
echo "RGOS-INIT: cannot find/mount an ext4 root with /sbin/init"
echo "--- block devices:"; cat /proc/partitions
echo "--- filesystems:"; cat /proc/filesystems
echo "--- dmesg tail:"; dmesg | tail -25
echo "RGOS-INIT: dropping to a shell"
exec setsid cttyhack sh
INIT
  chmod +x "$d/init"
  ( cd "$d" && find . | cpio -o -H newc --quiet | gzip -9 ) > "$OUT/initrd.gz"
}
# Everything that goes on the FAT32 boot partition, staged in $OUT/boot (used by the image AND the update tar):
# kernel, initramfs, DTB and extlinux.conf.
bootfiles() {
  local B="$OUT/boot" EXTRA=""
  [ -f "$OUT/Image" ] || { echo "no $OUT/Image: run ./build.sh kernel first"; exit 1; }
  [ -f "$RGOS_DTB" ] || { echo "missing DTB: $RGOS_DTB"; exit 1; }
  initrd
  rm -rf "$B"; mkdir -p "$B/extlinux"
  cp "$OUT/Image" "$OUT/initrd.gz" "$B/"
  # keep any extra kernel args ROCKNIX uses for this hardware (minus its own root/boot handling)
  if [ -f "$ROCKNIX_DIR/extlinux.conf" ]; then
    EXTRA=$({ grep -i -m1 '^[[:space:]]*append' "$ROCKNIX_DIR/extlinux.conf" \
      | sed 's/^[[:space:]]*[Aa][Pp][Pp][Ee][Nn][Dd][[:space:]]*//' | tr ' ' '\n' \
      | grep -Ev '^(boot=|disk=|quiet$|root=|rootwait$|rw$|ro$|console=|fsck|$)' | tr '\n' ' '; } || true)
    echo "extra kernel args from ROCKNIX: ${EXTRA:-<none>}"
  fi
  # The ROCKNIX joypad driver does not bind on this kernel, so expose the (plain GPIO) buttons through the
  # standard gpio-keys driver instead. Set RGOS_JOYPAD=driver to boot the DTB untouched.
  if [ "${RGOS_JOYPAD:-gpio}" = gpio ]; then
    python3 "$ROOT/tools/dtb_gpiokeys.py" "$RGOS_DTB" "$OUT/rgos.dtb"
    cp "$OUT/rgos.dtb" "$B/rgos.dtb"
  else
    cp "$RGOS_DTB" "$B/rgos.dtb"
  fi
  cat > "$B/extlinux/extlinux.conf" <<EXT
default rgos
label rgos
  linux /Image
  initrd /initrd.gz
  fdt /rgos.dtb
  append root=/dev/mmcblk0p2 rootwait rw console=tty0 quiet loglevel=3 systemd.show_status=0 vt.global_cursor_default=0 fsck.mode=skip $EXTRA
EXT
}
# Update package: out/rgos-update-<BUILD_ID>.tar = manifest + SHA256SUMS + boot files + gzip'd root filesystem.
# The big rootfs.tar.gz is last so the small members can be read quickly on the device (tools/rgos-update).
make_update_tar() {
  need tar gzip sha256sum
  [ -d "$WORK/rootfs" ] && [ -f "$OUT/boot/Image" ] || { echo "nothing to package: build the rootfs and boot files first"; exit 1; }
  local S="$WORK/update-stage" T="$OUT/rgos-update-$BUILD_ID.tar" kb nfiles
  rm -rf "$S"; mkdir -p "$S"; cp -r "$OUT/boot" "$S/boot"
  kb=$(sudo du -sk "$WORK/rootfs" | cut -f1)
  nfiles=$(sudo find "$WORK/rootfs" -xdev | wc -l)
  echo "packing root filesystem (${kb} KB, ${nfiles} files), this takes a minute..."
  sudo tar -C "$WORK/rootfs" --numeric-owner --one-file-system -cpf - . | gzip -6 > "$S/rootfs.tar.gz"
  cat > "$S/manifest" <<MANIFEST
FORMAT=1
TARGET=rgos-$DEVICE
BOOTIMG=$([ -f "$OUT/boot/boot.img" ] && echo 1 || echo 0)
BUILD_ID=$BUILD_ID
ROOTFS_KB=$kb
ROOTFS_FILES=$nfiles
MANIFEST
  local bi=; [ -f "$OUT/boot/boot.img" ] && bi=boot/boot.img
  ( cd "$S" && sha256sum boot/Image boot/initrd.gz boot/rgos.dtb boot/extlinux/extlinux.conf $bi rootfs.tar.gz > SHA256SUMS )
  rm -f "$T"
  tar -C "$S" -cf "$T" manifest SHA256SUMS boot/Image boot/initrd.gz boot/rgos.dtb boot/extlinux/extlinux.conf $bi rootfs.tar.gz
  rm -rf "$S"
  echo "Update package: $T ($(du -m "$T" | cut -f1) MB, build $BUILD_ID)"
  echo "  -> copy it into the 'update' folder on the ROMS partition and reboot the device."
}
# Rebuild only the update package after changing overlay/ (or after re-running the kernel stage).
update() {
  [ -d "$WORK/rootfs" ] || { echo "no rootfs: run ./build.sh rootfs first"; exit 1; }
  [ -x "$WORK/rootfs/usr/bin/retroarch" ] || { echo "this rootfs has no RetroArch: re-run ./build.sh rootfs once (it now installs retroarch + libretro-mgba)"; exit 1; }
  BUILD_ID=${BUILD_ID:-$(date -u +%Y%m%d-%H%M%S)}
  apply_overlay
  bootfiles
  make_update_tar
}
image() {
  need sfdisk losetup mkfs.vfat mkfs.ext4 tar gzip sha256sum
  [ -d "$WORK/rootfs" ] || { echo "no rootfs: run ./build.sh rootfs first"; exit 1; }
  BUILD_ID=${BUILD_ID:-$(date -u +%Y%m%d-%H%M%S)}
  bootfiles     # kernel, initramfs, DTB, extlinux.conf -> $OUT/boot (also checks they exist before touching a loop device)
  # --- size the root partition from what is actually in the rootfs ---
  need_mb=$(sudo du -sm "$WORK/rootfs" | cut -f1)
  nfiles=$(sudo find "$WORK/rootfs" -xdev | wc -l)
  min_root=$(( need_mb * 104 / 100 + 24 ))         # ext4 metadata/journal overhead + slack
  if [ "$ROOT_MB" = auto ]; then
    ROOT_MB=$(( need_mb * 104 / 100 + ROOT_HEADROOM_MB ))
  elif [ "$ROOT_MB" -lt "$min_root" ]; then
    echo "rootfs is ${need_mb} MB but ROOT_MB=$ROOT_MB is too small (need >= $min_root); raise ROOT_MB or use ROOT_MB=auto"; exit 1
  fi
  [ "$IMG_MB" = auto ] && IMG_MB=$(( 16 + BOOT_MB + ROOT_MB ))
  [ "$IMG_MB" -ge $(( 16 + BOOT_MB + ROOT_MB )) ] || { echo "IMG_MB=$IMG_MB is smaller than boot+root"; exit 1; }
  echo "sizes: rootfs ${need_mb} MB (${nfiles} files) -> root ${ROOT_MB} MiB, boot ${BOOT_MB} MiB, image ${IMG_MB} MiB (exFAT ROMS is created on the device at first boot)"
  DEVICE_SLUG=${DEVICE//-/}
  IMG="$OUT/rgos-${DEVICE_SLUG}.img"; rm -f "$IMG"; truncate -s "${IMG_MB}M" "$IMG"
  sfdisk "$IMG" <<PT
label: dos
start=16MiB, size=${BOOT_MB}MiB, type=c, bootable
start=$((16+BOOT_MB))MiB, size=${ROOT_MB}MiB, type=83
PT
  LOOP=$(sudo losetup -fP --show "$IMG")
  trap 'sudo umount -R "$WORK/mnt" 2>/dev/null; sudo losetup -d "$LOOP"' EXIT
  # compact ext4: no reserved-for-root blocks, small journal (kept for power-loss safety), inode count sized to the
  # rootfs (+30%) instead of the default ratio
  sudo mkfs.vfat -F 32 -n RGBOOT "${LOOP}p1"
  sudo mkfs.ext4 -q -L rgos-root -m 0 -J size=8 -N $(( nfiles * 13 / 10 + 2000 )) "${LOOP}p2"
  mkdir -p "$WORK/mnt"; sudo mount "${LOOP}p2" "$WORK/mnt"
  sudo cp -a "$WORK/rootfs/." "$WORK/mnt/"
  printf 'BUILD_ID=%s\n' "$BUILD_ID" | sudo tee "$WORK/mnt/etc/rgos-release" >/dev/null
  sudo mount "${LOOP}p1" "$WORK/mnt/boot"
  sudo cp -r "$OUT/boot/." "$WORK/mnt/boot/"
  sudo umount "$WORK/mnt/boot" "$WORK/mnt"
  sudo dd if="$OUT/u-boot-sunxi-with-spl.bin" of="$LOOP" bs=1024 seek=8 conv=fsync status=none
  sudo losetup -d "$LOOP"; trap - EXIT
  echo "Done: $IMG  (flash with: sudo dd if=$IMG of=/dev/sdX bs=4M conv=fsync status=progress)"
  make_update_tar
}

need git make gcc sfdisk; [ -n "$CROSS" ] && need ${CROSS}gcc
case "${1:-all}" in
  tfa) tfa;; uboot) uboot;; kernel) kernel;; rootfs) rootfs;; image) image;; update) update;;
  all) tfa; uboot; kernel; rootfs; image;;
  *) echo "unknown stage/device: ${1:-}"; echo "usage: ./build all | ./build.sh [rg34xxsp|rg35xxsp] [all|tfa|uboot|kernel|rootfs|image|update]"; exit 1;;
esac
