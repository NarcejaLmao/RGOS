#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSET_DIR="$ROOT/assets/rocknix"
CACHE_DIR="$ROOT/downloads"
DDR_VARIANT="${RGOS_H700_DDR:-DDR4}"
REPO_API="https://api.github.com/repos/ROCKNIX/distribution/releases/latest"

for tool in python3 gzip sfdisk mdir mcopy mlabel dd unsquashfs; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "Missing required tool: $tool" >&2
    echo "Install with: sudo apt-get install curl python3 gzip fdisk mtools squashfs-tools" >&2
    exit 2
  }
done
mkdir -p "$ASSET_DIR" "$CACHE_DIR"

if [[ -n "${RGOS_UPSTREAM_IMG:-}" ]]; then
  # Use a local, already-decompressed upstream image (offline builds / tests).
  [[ -f "$RGOS_UPSTREAM_IMG" ]] || { echo "RGOS_UPSTREAM_IMG not found: $RGOS_UPSTREAM_IMG" >&2; exit 2; }
  RELEASE_TAG="local"; IMAGE_NAME="$(basename "$RGOS_UPSTREAM_IMG")"; IMAGE_URL="file://$RGOS_UPSTREAM_IMG"
  RAW_IMAGE="$RGOS_UPSTREAM_IMG"
else
  readarray -t RELEASE_DATA < <(python3 - "$REPO_API" "$DDR_VARIANT" <<'PY'
import json, sys, urllib.request
url, variant = sys.argv[1:]
req = urllib.request.Request(url, headers={"User-Agent": "RGOS-RG34XXSP-build"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.load(r)
needle = f"ROCKNIX-H700.aarch64-"
assets = [a for a in data.get("assets", []) if a["name"].startswith(needle) and a["name"].endswith(".img.gz")]
if variant.upper() in ("DDR3", "DDR4"):
    exact = [a for a in assets if f"-{variant.upper()}.img.gz" in a["name"]]
else:
    exact = []
if exact:
    asset = exact[0]
elif assets:
    assets.sort(key=lambda a: a["name"], reverse=True)
    asset = assets[0]
else:
    raise SystemExit("No ROCKNIX H700 .img.gz asset found in latest release")
print(data.get("tag_name", "unknown"))
print(asset["name"])
print(asset["browser_download_url"])
PY
  )
  RELEASE_TAG="${RELEASE_DATA[0]}"
  IMAGE_NAME="${RELEASE_DATA[1]}"
  IMAGE_URL="${RELEASE_DATA[2]}"
  ARCHIVE="$CACHE_DIR/$IMAGE_NAME"
  RAW_IMAGE="$CACHE_DIR/${IMAGE_NAME%.gz}"

  command -v curl >/dev/null || { echo "Missing required tool: curl" >&2; exit 2; }
  printf 'Using ROCKNIX release: %s (%s)\n' "$RELEASE_TAG" "$IMAGE_NAME"
  if [[ ! -s "$ARCHIVE" ]]; then
    echo "Downloading upstream H700 image (large download)..."
    curl -fL --retry 3 --continue-at - "$IMAGE_URL" -o "$ARCHIVE"
  fi
  if [[ ! -s "$RAW_IMAGE" || "$RAW_IMAGE" -ot "$ARCHIVE" ]]; then
    echo "Decompressing upstream image; this needs several GB of free disk space..."
    gzip -dkf "$ARCHIVE"
  fi
fi

# Locate the FAT boot partition without mounting it or requiring root privileges.
PART_JSON="$(sfdisk --json "$RAW_IMAGE")"
BOOT_OFFSET="$(python3 - "$PART_JSON" <<'PY'
import json, sys
j = json.loads(sys.argv[1])
sectorsize = j["partitiontable"].get("sectorsize", 512)
for p in j["partitiontable"].get("partitions", []):
    start = int(p["start"]) * sectorsize
    # mtools' image-offset syntax lets us inspect FAT partitions unprivileged.
    print(start)
PY
)"

# ---- Allwinner bootloader (SPL + U-Boot) -----------------------------------------------------------------------
# The H700 BROM only looks for the "eGON.BT0" SPL at byte 8192 of the SD card. Without it the console powers on
# to NOTHING: no LEDs, no screen. It lives between the MBR and the first partition, so it has to be copied from the
# upstream image (sectors 16 .. first-partition-start-1).
LABEL_TYPE="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["partitiontable"].get("label",""))' "$PART_JSON")"
if [[ "$LABEL_TYPE" != "dos" ]]; then
  echo "Upstream image uses a '$LABEL_TYPE' partition table; the bootloader copy assumes MBR (dos)." >&2
  exit 5
fi
FIRST_START="$(python3 -c 'import json,sys; print(min(int(p["start"]) for p in json.loads(sys.argv[1])["partitiontable"]["partitions"]))' "$PART_JSON")"
if (( FIRST_START <= 16 )); then echo "Unexpected first partition start: $FIRST_START" >&2; exit 5; fi
dd if="$RAW_IMAGE" of="$ASSET_DIR/bootloader.bin" bs=512 skip=16 count=$((FIRST_START - 16)) status=none
if [[ "$(dd if="$ASSET_DIR/bootloader.bin" bs=1 skip=4 count=8 status=none)" != "eGON.BT0" ]]; then
  echo "No eGON.BT0 SPL found at byte 8192 of $RAW_IMAGE - cannot extract the bootloader." >&2
  exit 6
fi
printf '%s\n' "$FIRST_START" > "$ASSET_DIR/first-partition-start"
echo "Extracted Allwinner bootloader: $(stat -c%s "$ASSET_DIR/bootloader.bin") bytes (upstream first partition at sector $FIRST_START)."

STAGE="$ASSET_DIR/extracted"
rm -rf "$STAGE"
mkdir -p "$STAGE"
FOUND=0
while IFS= read -r OFFSET; do
  [[ "$OFFSET" =~ ^[0-9]+$ ]] || continue
  if mdir -i "${RAW_IMAGE}@@${OFFSET}" ::/ >/dev/null 2>&1; then
    echo "Found FAT partition at byte offset $OFFSET; extracting boot files..."
    mkdir -p "$STAGE/boot-partition"
    # mcopy copies the top-level contents; directory trees are copied recursively.
    (cd "$STAGE/boot-partition" && mcopy -s -i "${RAW_IMAGE}@@${OFFSET}" ::/* .) || true
    mlabel -i "${RAW_IMAGE}@@${OFFSET}" -s :: 2>/dev/null | sed -n 's/^Volume label is \([^ ]*\).*/\1/p' > "$ASSET_DIR/boot-label" || true
    FOUND=1
    break
  fi
done <<< "$BOOT_OFFSET"

if [[ "$FOUND" -ne 1 ]]; then
  echo "Could not identify a FAT boot partition in $IMAGE_NAME." >&2
  echo "The upstream image is cached at: $RAW_IMAGE" >&2
  exit 3
fi

# ---- kernel modules --------------------------------------------------------------------------------------------
# ROCKNIX keeps its modules (gpio-keys, ADC, PWM, joypad, uinput, ...) inside the SYSTEM squashfs, not in the kernel image.
# Without them the buttons never appear. Extract every lib/modules/<version> tree to assets/rocknix/modules/.
MODDIR="$ASSET_DIR/modules"; rm -rf "$MODDIR" "$ASSET_DIR/.sq"; mkdir -p "$MODDIR"
SYSTEM_FILE="$STAGE/boot-partition/SYSTEM"
if [[ -f "$SYSTEM_FILE" ]]; then
  unsquashfs -l "$SYSTEM_FILE" 2>/dev/null | sed 's#^squashfs-root/##' > "$ASSET_DIR/system.list" || true
  VERDIRS="$(grep -E '(^|/)lib/modules/[^/]+/.*\.ko' "$ASSET_DIR/system.list" | sed -E 's#(.*lib/modules/[^/]+)/.*#\1#' | sort -u || true)"
  for vd in $VERDIRS; do
    unsquashfs -n -f -d "$ASSET_DIR/.sq" "$SYSTEM_FILE" "$vd" >/dev/null 2>&1 || true
    [[ -d "$ASSET_DIR/.sq/$vd" ]] && cp -a "$ASSET_DIR/.sq/$vd" "$MODDIR/"
  done
  rm -rf "$ASSET_DIR/.sq" "$ASSET_DIR/system.list"
  echo "Kernel modules: $(ls "$MODDIR" | tr '\n' ' ')($(find "$MODDIR" -name '*.ko*' | wc -l) files)"
  find "$MODDIR" -name '*.ko*' | grep -iE 'joypad|adc|pwm|gpio-keys' | sed 's#.*/#  driver: #' || true
else
  echo "WARNING: no SYSTEM file in the ROCKNIX boot partition; cannot extract kernel modules (buttons will not work)." >&2
fi

DTB="$(find "$STAGE/boot-partition" -type f -name 'sun50i-h700-anbernic-rg34xx-sp.dtb' -print -quit)"
if [[ -z "$DTB" ]]; then
  echo "RG34XXSP DTB not found in the extracted boot partition." >&2
  echo "Inspect $STAGE/boot-partition and the device_trees directory in the upstream image." >&2
  exit 4
fi
cp -f "$DTB" "$ASSET_DIR/sun50i-h700-anbernic-rg34xx-sp.dtb"
printf '%s\n' "$RELEASE_TAG" > "$ASSET_DIR/UPSTREAM_RELEASE"
printf '%s\n' "$IMAGE_NAME" > "$ASSET_DIR/UPSTREAM_IMAGE"
printf '%s\n' "$IMAGE_URL" > "$ASSET_DIR/UPSTREAM_URL"
echo "Fetched RG34XXSP boot assets from ROCKNIX $RELEASE_TAG."
echo "DTB: $ASSET_DIR/sun50i-h700-anbernic-rg34xx-sp.dtb"
echo "Boot files staged at: $STAGE/boot-partition"
echo "NOTE: this fetches and stages upstream assets; it does not by itself make a compatible RG OS boot chain."
