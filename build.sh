#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BR_DIR="${BUILDROOT_DIR:-$ROOT/buildroot}"
ASSET_DIR="${ROCKNIX_ASSET_DIR:-$ROOT/assets/rocknix}"
BOOT_DIR="${ROCKNIX_BOOT_DIR:-$ASSET_DIR/extracted/boot-partition}"
ROOTFS_OVERRIDE="${RGOS_ROOTFS:-$ROOT/output/rgos-rg34xxsp-rootfs.ext4}"
OUT_DIR="${RGOS_OUT_DIR:-$ROOT/output}"
IMAGE="${RGOS_IMAGE:-$OUT_DIR/rgos-rg34xxsp.img}"
RGOS_BUILD_ID="${RGOS_BUILD_ID:-$(date -u +%Y%m%d-%H%M%S)}"
UPDATE_TAR="${RGOS_UPDATE_TAR:-$OUT_DIR/rgos-update-$RGOS_BUILD_ID.tar}"
BOOT_SIZE_MIB="${RGOS_BOOT_SIZE_MIB:-512}"
ROOTFS_EXTRA_MIB="${RGOS_ROOTFS_EXTRA_MIB:-16}"
ROMS_MIB="${RGOS_ROMS_MIB:-512}"          # size of the exFAT ROMS partition (partition 3); 0 = none

usage() {
  cat <<USAGE
Usage: $0 {prepare|fetch-assets|build|image|update|help}
  prepare  Clone Buildroot if missing.
  fetch-assets  Download ROCKNIX H700 image; stage boot files, DTB and the Allwinner bootloader.
  build    Build Buildroot rootfs and assemble a whole-disk .img.
  image    Assemble a .img from an already-built rootfs and staged ROCKNIX boot files.
  update   Rebuild the rootfs and pack an update .tar (output/rgos-update-<id>.tar). Copy it into the update/ folder of the
           exFAT/FAT ROMS partition; the device installs it on the next boot. RGOS_SKIP_BUILDROOT=1 re-packs without running
           Buildroot, RGOS_UPDATE_SLIM=1 leaves the kernel out, RGOS_BUILD_ID=... sets the id.

Environment:
  BUILDROOT_DIR       Buildroot source directory (default: ./buildroot)
  ROCKNIX_ASSET_DIR   ROCKNIX asset directory (default: ./assets/rocknix)
  ROCKNIX_BOOT_DIR    Extracted boot partition directory
  ROCKNIX_IMAGE       Optional original ROCKNIX .img; copies raw bootloader sectors
  RGOS_ROOTFS         Rootfs ext4 path override
  RGOS_IMAGE          Output image path override
  RGOS_BOOT_LABEL     FAT label of the boot partition (default RGOS)
  RGOS_ROMS_MIB       Size of the exFAT ROMS partition in the image (default 512, 0 = none). Needs mkfs.exfat (apt install exfatprogs)
  RGOS_BUILD_ID       Build id stamped into the image / update (default: UTC timestamp)
USAGE
}

find_rootfs() {
  local candidates=(
    "$ROOTFS_OVERRIDE"
    "$BR_DIR/output/images/rootfs.ext4"
    "$BR_DIR/output/images/rootfs.ext4.gz"
    "$ROOT/output/rgos-rg34xxsp-rootfs.ext4"
  )
  local f
  for f in "${candidates[@]}"; do
    if [[ -f "$f" ]]; then
      if [[ "$f" == *.gz ]]; then
        local expanded="$OUT_DIR/rootfs.ext4"
        mkdir -p "$OUT_DIR"
        gzip -dc "$f" > "$expanded"
        echo "$expanded"
      else
        echo "$f"
      fi
      return 0
    fi
  done
  echo "ERROR: Could not find an ext4 rootfs. Set RGOS_ROOTFS=/path/to/rootfs.ext4." >&2
  return 1
}

# Buildroot no longer ships a python-pygame package, so use the official prebuilt pygame wheel for aarch64 (it bundles SDL2,
# SDL_image/mixer/ttf, freetype, ...) and unpack it into an extra rootfs overlay. Needs the glibc toolchain (manylinux wheel).
fetch_pygame() {
  local mk="$BR_DIR/package/python3/python3.mk" pyver dest tmp pkg ok=0
  pyver="$(sed -n 's/^PYTHON3_VERSION_MAJOR *= *\([0-9.]*\).*/\1/p' "$mk" 2>/dev/null | head -n 1)"
  [[ -n "$pyver" ]] || { echo "ERROR: cannot read the Python version from $mk" >&2; exit 1; }
  dest="$ROOT/buildroot-external/board/rgos/pygame-overlay/usr/lib/python$pyver/site-packages"
  if [[ -d "$dest/pygame" ]]; then echo "pygame wheel already unpacked for Python $pyver."; return 0; fi
  command -v unzip >/dev/null || { echo "ERROR: install unzip" >&2; exit 1; }
  python3 -m pip --version >/dev/null 2>&1 || { echo "ERROR: install pip: sudo apt install python3-pip" >&2; exit 1; }
  tmp="$(mktemp -d)"
  for pkg in pygame pygame-ce; do
    echo "Downloading $pkg wheel for aarch64 / CPython $pyver ..."
    if python3 -m pip download --no-deps --only-binary=:all: --disable-pip-version-check \
         --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 --platform manylinux_2_28_aarch64 \
         --python-version "$pyver" --implementation cp --abi "cp${pyver/./}" -d "$tmp" "$pkg" >/dev/null 2>"$tmp/err"; then ok=1; break; fi
    tail -n 3 "$tmp/err" >&2
  done
  [[ "$ok" = 1 ]] || { echo "ERROR: no aarch64 pygame wheel for Python $pyver (see above)." >&2; exit 1; }
  rm -rf "$ROOT/buildroot-external/board/rgos/pygame-overlay"; mkdir -p "$dest"
  unzip -q -o "$tmp"/*.whl -d "$dest"
  rm -rf "$tmp"
  echo "Unpacked pygame into $dest"
}

# Stage the files that live on the FAT boot partition (kernel, launcher initramfs + modules, DTB, extlinux.conf) in
# $OUT_DIR/boot-stage and point BOOT_DIR at it. Used by both the full image and the update tar.
stage_boot() {
  # --- Stage the boot files: ROCKNIX kernel + RG OS initramfs (launcher) + RG OS device tree + our own extlinux.conf ---
  local initrd stage="$OUT_DIR/boot-stage"
  initrd="$OUT_DIR/rgos-initrd.gz"
  [[ -f "$BR_DIR/output/images/rootfs.cpio.gz" ]] || { echo "ERROR: no initramfs ($BR_DIR/output/images/rootfs.cpio.gz). Run ./build.sh build." >&2; exit 1; }
  cp -f "$BR_DIR/output/images/rootfs.cpio.gz" "$initrd"
  # ROCKNIX kernel modules (input/ADC/PWM/uinput drivers) go in as a second, concatenated initramfs archive.
  if [[ -d "$ASSET_DIR/modules" ]] && find "$ASSET_DIR/modules" -name '*.ko*' -print -quit | grep -q .; then
    command -v cpio >/dev/null || { echo "ERROR: install cpio" >&2; exit 1; }
    local mtmp="$OUT_DIR/modules-cpio"; rm -rf "$mtmp"; mkdir -p "$mtmp/lib/modules"
    local filt="${RGOS_MODULE_FILTER:-kernel/(drivers/(input|iio|pwm|gpio|mfd|misc|hid)|lib|crypto|fs/(exfat|fat|nls))/}"
    local ver
    for ver in "$ASSET_DIR"/modules/*/; do
      ver="$(basename "$ver")"; mkdir -p "$mtmp/lib/modules/$ver"
      cp -a "$ASSET_DIR/modules/$ver"/modules.* "$mtmp/lib/modules/$ver/" 2>/dev/null || true
      ( cd "$ASSET_DIR/modules/$ver" && find . -type f -name '*.ko*' | grep -E "$filt" | cpio -pdm --quiet "$mtmp/lib/modules/$ver" )
    done
    ( cd "$mtmp" && find . | cpio -o -H newc --quiet | gzip -9 ) > "$OUT_DIR/rgos-modules.cpio.gz"
    cat "$OUT_DIR/rgos-modules.cpio.gz" >> "$initrd"
    echo "Added $(find "$mtmp" -name '*.ko*' | wc -l) kernel modules to the initramfs."
  else
    echo "WARNING: no kernel modules in $ASSET_DIR/modules - run ./build.sh fetch-assets (needs squashfs-tools). Buttons will not work." >&2
  fi
  rm -rf "$stage"; mkdir -p "$stage/extlinux"
  python3 - "$BOOT_DIR" "$stage" <<'PYEOF'
import os, re, shutil, sys
src, stage = sys.argv[1:3]
kern = next((f for f in ("KERNEL", "Image") if os.path.isfile(os.path.join(src, f))), None)
if not kern:
    sys.exit("ERROR: no KERNEL/Image in the ROCKNIX boot files: " + src)
shutil.copy2(os.path.join(src, kern), os.path.join(stage, kern))
# keep ROCKNIX's extra kernel arguments for this hardware, minus its own root/boot handling (same as the RG OS image builder)
extra = ""
for r, _, fs in os.walk(src):
    for f in fs:
        if f.lower() == "extlinux.conf":
            for l in open(os.path.join(r, f), errors="replace"):
                m = re.match(r"\s*append\s+(.*)", l, re.I)
                if m:
                    keep = [t for t in m.group(1).split() if not re.match(r"(boot=|disk=|quiet$|root=|rootwait$|rw$|ro$|console=|fsck|rdinit=)", t)]
                    extra = " ".join(keep)
                    break
open(os.path.join(stage, "extlinux", "extlinux.conf"), "w").write(
    "default rgos\nlabel rgos\n  linux /%s\n  initrd /rgos-initrd.gz\n  fdt /rgos.dtb\n"
    "  append rdinit=/init rw console=tty0 quiet loglevel=3 audit=0 vt.global_cursor_default=0 %s\n" % (kern, extra))
print(open(os.path.join(stage, "extlinux", "extlinux.conf")).read())
PYEOF
  cp -f "$initrd" "$stage/rgos-initrd.gz"
  # Device tree: RG OS DTB with the buttons moved to the standard gpio-keys driver (the ROCKNIX joypad driver does not bind)
  local dtb="${RGOS_DTB:-$ROOT/dtb/sun50i-h700-anbernic-rg34xx-sp.dtb}"
  [[ -f "$dtb" ]] || { echo "ERROR: missing DTB: $dtb" >&2; exit 1; }
  if [[ "${RGOS_JOYPAD:-gpio}" == gpio ]]; then python3 "$ROOT/tools/dtb_gpiokeys.py" "$dtb" "$stage/rgos.dtb"; else cp -f "$dtb" "$stage/rgos.dtb"; fi
  BOOT_DIR="$stage"
  printf '%s\n' "$RGOS_BUILD_ID" > "$stage/rgos-update.id"
}

assemble_image() {
  local rootfs bootfs rootfs_bytes rootfs_mib boot_start boot_sectors root_start root_sectors roms_start roms_sectors total_sectors label bl_bytes bl_sectors
  rootfs="$(find_rootfs)"
  [[ -d "$BOOT_DIR" ]] || { echo "ERROR: ROCKNIX boot files not found: $BOOT_DIR (run ./build.sh fetch-assets)" >&2; exit 1; }
  for t in sfdisk mkfs.vfat mcopy mkfs.ext4 $( ((ROMS_MIB > 0)) && echo mkfs.exfat ); do
    command -v "$t" >/dev/null || { echo "ERROR: missing host tool: $t (apt install fdisk dosfstools mtools e2fsprogs exfatprogs)" >&2; exit 1; }
  done

  # --- Allwinner bootloader: MANDATORY. Without the eGON.BT0 SPL at byte 8192 the H700 BROM finds nothing to run and
  # the device stays completely dead (no LEDs, no screen).
  local blfile="$ASSET_DIR/bootloader.bin" upstart="$ASSET_DIR/first-partition-start"
  if [[ -n "${ROCKNIX_IMAGE:-}" && -f "$ROCKNIX_IMAGE" ]]; then
    local fs; fs="$(sfdisk -d "$ROCKNIX_IMAGE" | sed -n 's/.*start= *\([0-9]*\),.*/\1/p' | sort -n | head -n 1)"
    blfile="$OUT_DIR/bootloader.bin"; mkdir -p "$OUT_DIR"
    dd if="$ROCKNIX_IMAGE" of="$blfile" bs=512 skip=16 count=$((fs - 16)) status=none
    upstart="$OUT_DIR/first-partition-start"; echo "$fs" > "$upstart"
  fi
  [[ -s "$blfile" ]] || { echo "ERROR: no Allwinner bootloader ($blfile). Run ./build.sh fetch-assets (or set ROCKNIX_IMAGE). An image without it will NOT power on." >&2; exit 1; }
  [[ "$(dd if="$blfile" bs=1 skip=4 count=8 status=none)" == "eGON.BT0" ]] || { echo "ERROR: $blfile has no eGON.BT0 SPL header." >&2; exit 1; }
  bl_bytes="$(stat -c %s "$blfile")"
  bl_sectors=$(( (bl_bytes + 511) / 512 ))

  # Boot partition starts where upstream's does (never below 2048, never inside the bootloader).
  boot_start="$(cat "$upstart" 2>/dev/null || echo 2048)"
  (( boot_start >= 2048 )) || boot_start=2048
  (( boot_start >= 16 + bl_sectors )) || boot_start=$(( ((16 + bl_sectors + 2047) / 2048) * 2048 ))
  stage_boot

  # Size the FAT partition from what is really in it (ROCKNIX keeps its multi-hundred-MB SYSTEM image there).
  local boot_bytes need_mib
  boot_bytes="$(du -sb --apparent-size "$BOOT_DIR" | cut -f1)"
  need_mib=$(( (boot_bytes * 105 / 100 + 1048575) / 1048576 + 64 ))
  if (( need_mib > BOOT_SIZE_MIB )); then
    echo "Boot files need $((boot_bytes / 1048576)) MiB; growing FAT partition from ${BOOT_SIZE_MIB} to ${need_mib} MiB."
    BOOT_SIZE_MIB=$need_mib
  fi
  boot_sectors=$((BOOT_SIZE_MIB * 2048))
  root_start=$(( ((boot_start + boot_sectors + 2047) / 2048) * 2048 ))
  rootfs_bytes="$(stat -c '%s' "$rootfs")"
  rootfs_mib=$(( (rootfs_bytes + 1048575) / 1048576 ))
  root_sectors=$(( (rootfs_mib + ROOTFS_EXTRA_MIB) * 2048 ))
  roms_start=$(( ((root_start + root_sectors + 2047) / 2048) * 2048 )); roms_sectors=$(( ROMS_MIB * 2048 ))
  if (( ROMS_MIB > 0 )); then total_sectors=$(( roms_start + roms_sectors )); else total_sectors=$(( root_start + root_sectors )); fi

  label="${RGOS_BOOT_LABEL:-RGOS}"        # FAT volume label of the boot partition (max 11 chars); no longer copied from ROCKNIX
  mkdir -p "$OUT_DIR"
  bootfs="$OUT_DIR/rgos-boot-partition.fat"
  echo "Creating FAT boot partition (${BOOT_SIZE_MIB} MiB, label $label)..."
  rm -f "$bootfs"; truncate -s "${BOOT_SIZE_MIB}M" "$bootfs"
  mkfs.vfat -F 32 -n "$label" "$bootfs" >/dev/null
  shopt -s dotglob nullglob
  local files=("$BOOT_DIR"/*)
  shopt -u dotglob nullglob
  ((${#files[@]})) || { echo "ERROR: boot partition directory is empty: $BOOT_DIR" >&2; exit 1; }
  mcopy -i "$bootfs" -s "${files[@]}" ::/

  local romsfs="$OUT_DIR/rgos-roms-partition.exfat"
  if (( ROMS_MIB > 0 )); then
    echo "Creating exFAT ROMS partition (${ROMS_MIB} MiB, label ROMS)..."
    rm -f "$romsfs"; truncate -s "${ROMS_MIB}M" "$romsfs"
    mkfs.exfat -L ROMS "$romsfs" >/dev/null
  fi
  echo "Creating whole-disk image: $IMAGE"
  rm -f "$IMAGE"
  truncate -s $((total_sectors * 512)) "$IMAGE"
  sfdisk "$IMAGE" <<PARTITIONS
label: dos
unit: sectors

start=$boot_start, size=$boot_sectors, type=c, bootable
start=$root_start, size=$root_sectors, type=83
$( ((ROMS_MIB > 0)) && echo "start=$roms_start, size=$roms_sectors, type=7" )
PARTITIONS
  # Bootloader goes at byte 8192 (sector 16), AFTER the partition table is written and below the first partition.
  dd if="$blfile" of="$IMAGE" bs=512 seek=16 conv=notrunc status=none
  dd if="$bootfs" of="$IMAGE" bs=512 seek="$boot_start" conv=notrunc status=none
  dd if="$rootfs" of="$IMAGE" bs=512 seek="$root_start" conv=notrunc status=none
  (( ROMS_MIB > 0 )) && dd if="$romsfs" of="$IMAGE" bs=512 seek="$roms_start" conv=notrunc status=none
  sync

  # Final sanity check on the finished image.
  [[ "$(dd if="$IMAGE" bs=1 skip=8196 count=8 status=none)" == "eGON.BT0" ]] || { echo "ERROR: verification failed: no SPL at 8 KiB in $IMAGE" >&2; exit 1; }
  [[ "$(dd if="$IMAGE" bs=1 skip=510 count=2 status=none | od -An -tx1 | tr -d ' \n')" == "55aa" ]] || { echo "ERROR: verification failed: bad MBR signature" >&2; exit 1; }
  echo
  echo "Whole-disk image created: $IMAGE"
  ls -lh "$IMAGE"
  echo "Layout: MBR; bootloader at 8 KiB (${bl_bytes} bytes); FAT '$label' at sector $boot_start; ext4 rootfs at sector $root_start."
  (( ROMS_MIB > 0 )) && echo "       exFAT 'ROMS' (${ROMS_MIB} MiB) at sector $roms_start - this is the partition Windows shows; put update/*.tar and roms/ there."
  return 0
}

buildroot_make() {
    # WSL appends Windows directories ("/mnt/c/Program Files/...") to PATH; Buildroot refuses any PATH with whitespace.
    CLEAN_PATH=""
    IFS=: read -ra _P <<< "$PATH"
    for _d in "${_P[@]}"; do
      case "$_d" in *[[:space:]]*|/mnt/[a-z]/*|"") continue ;; esac
      CLEAN_PATH="${CLEAN_PATH:+$CLEAN_PATH:}$_d"
    done
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${CLEAN_PATH:+:$CLEAN_PATH}"
    fetch_pygame
    make -C "$BR_DIR" BR2_EXTERNAL="$ROOT/buildroot-external" rgos_rg34xxsp_defconfig
    make -C "$BR_DIR" -j"$(nproc)"
    # Buildroot's output path may differ by configuration; also accept the
    # project-specific rootfs path used by earlier RG OS build scripts.
    if [[ ! -f "$ROOTFS_OVERRIDE" && -f "$BR_DIR/output/images/rootfs.ext4" ]]; then
      mkdir -p "$(dirname "$ROOTFS_OVERRIDE")"
      cp "$BR_DIR/output/images/rootfs.ext4" "$ROOTFS_OVERRIDE"
    fi
}

# Unzipping on Windows (or working under /mnt/c) drops the executable bits; restore them for everything that has to run
# on the device (copied by the Buildroot overlay) or in the build.
fix_modes() {
  local o="$ROOT/buildroot-external/board/rgos/overlay"
  chmod +x "$o/usr/bin/systemctl" "$o/opt/rgos/rgos-session" "$o/opt/rgos/rgos-sticks" "$o/opt/rgos/rgos-update" \
           "$o/opt/rgos/menu.py" "$ROOT"/build.sh "$ROOT"/scripts/*.sh "$ROOT"/tools/*.py 2>/dev/null || true
}
fix_modes

case "${1:-help}" in
  fetch-assets)
    bash "$ROOT/scripts/fetch-rocknix-assets.sh"
    ;;
  prepare)
    if [[ ! -d "$BR_DIR" ]]; then
      git clone https://github.com/buildroot/buildroot.git "$BR_DIR"
    else
      echo "Using existing Buildroot: $BR_DIR"
    fi
    echo "Buildroot source prepared."
    ;;
  build)
    [[ -d "$BR_DIR" ]] || { echo "Buildroot not found. Run ./build.sh prepare or set BUILDROOT_DIR." >&2; exit 1; }
    buildroot_make
    [[ -s "$ASSET_DIR/bootloader.bin" && -d "$BOOT_DIR" && -d "$ASSET_DIR/modules" ]] || bash "$ROOT/scripts/fetch-rocknix-assets.sh"
    assemble_image
    ;;
  image)
    [[ ( -s "$ASSET_DIR/bootloader.bin" && -d "$ASSET_DIR/modules" ) || -n "${ROCKNIX_IMAGE:-}" ]] || bash "$ROOT/scripts/fetch-rocknix-assets.sh"
    assemble_image
    ;;
  update)
    [[ -d "$BR_DIR" ]] || { echo "Buildroot not found. Run ./build.sh prepare or set BUILDROOT_DIR." >&2; exit 1; }
    [[ -n "${RGOS_SKIP_BUILDROOT:-}" ]] || buildroot_make
    [[ -d "$BOOT_DIR" && -d "$ASSET_DIR/modules" ]] || bash "$ROOT/scripts/fetch-rocknix-assets.sh"
    stage_boot
    python3 "$ROOT/tools/mkupdate.py" "$BOOT_DIR" "$UPDATE_TAR" --id "$RGOS_BUILD_ID" \
      --device "$(tr -d '[:space:]' < "$ROOT/buildroot-external/board/rgos/overlay/etc/rgos-device")" ${RGOS_UPDATE_SLIM:+--no-kernel}
    echo
    echo "Copy $UPDATE_TAR into the update/ folder on the ROMS (exFAT) partition of the SD card and boot the device."
    ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
