#!/bin/bash
# RG OS Tools > PortMaster: install and run the official PortMaster application.
# The application is stored on the ROMS card so it survives firmware updates.
set -Eeuo pipefail

ROMS_ROOT="${ROMS_ROOT:-/media/roms}"
PORTS_DIR="${PORTS_DIR:-$ROMS_ROOT/roms/ports}"
PM_DIR="$PORTS_DIR/PortMaster"
LOG_DIR="${RGOS_PORT_SAVE_DIR:-$ROMS_ROOT/saves/ports/PortMaster}"
LOG="$LOG_DIR/portmaster.log"
mkdir -p "$LOG_DIR" "$PORTS_DIR"
exec > >(tee -a "$LOG") 2>&1

fail() {
    echo
    echo "PortMaster could not start: $*"
    echo "Log: $LOG"
    sleep 4
    exit 1
}

[ -d "$ROMS_ROOT/roms" ] || fail "ROMS partition is not mounted at $ROMS_ROOT"
command -v curl >/dev/null || fail 'curl is not installed'
command -v unzip >/dev/null || fail 'unzip is not installed'
command -v python3 >/dev/null || fail 'python3 is not installed'

# PortMaster and many port scripts expect /roms/ports. RG OS mounts the card
# at /media/roms and stores the ROM tree under /media/roms/roms.
if [ ! -e /roms ]; then
    ln -s "$ROMS_ROOT/roms" /roms 2>/dev/null || true
fi
[ -d /roms ] || fail 'could not provide the conventional /roms path'

if [ ! -x "$PM_DIR/PortMaster.sh" ]; then
    work=$(mktemp -d /tmp/rgos-portmaster.XXXXXX)
    trap 'rm -rf "$work"' EXIT
    echo 'Downloading the official PortMaster release metadata...'
    curl -fLsS --retry 2 --connect-timeout 15 \
      https://api.github.com/repos/PortsMaster/PortMaster-GUI/releases/latest \
      -o "$work/release.json" || fail 'cannot reach GitHub; connect Wi-Fi and retry'
    url=$(python3 - "$work/release.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    release = json.load(f)
for asset in release.get('assets', []):
    if asset.get('name') == 'PortMaster.zip':
        print(asset.get('browser_download_url', ''))
        break
PY
)
    [ -n "$url" ] || fail 'official release did not contain PortMaster.zip'
    echo "Downloading official PortMaster package..."
    curl -fLsS --retry 2 --connect-timeout 15 "$url" -o "$work/PortMaster.zip" \
      || fail 'PortMaster download failed; check Wi-Fi and retry'
    unzip -tq "$work/PortMaster.zip" >/dev/null || fail 'downloaded PortMaster ZIP is invalid'
    mkdir -p "$work/extract"
    unzip -q "$work/PortMaster.zip" -d "$work/extract" || fail 'could not extract PortMaster package'
    [ -f "$work/extract/PortMaster/PortMaster.sh" ] || fail 'official ZIP has an unexpected directory layout'

    # Preserve the autoinstall queue and locally-added runtime/library files across updates.
    mkdir -p "$PM_DIR"
    for keep in autoinstall libs runtimes; do
        if [ -d "$PM_DIR/$keep" ]; then
            mkdir -p "$work/extract/PortMaster/$keep"
            cp -an "$PM_DIR/$keep/." "$work/extract/PortMaster/$keep/" 2>/dev/null || true
        fi
    done
    # Replace only the PortMaster application directory, not ROMs, saves, or states.
    old="$PORTS_DIR/.PortMaster-old.$$"
    rm -rf "$old"
    [ ! -d "$PM_DIR" ] || mv "$PM_DIR" "$old"
    if mv "$work/extract/PortMaster" "$PM_DIR"; then
        rm -rf "$old"
    else
        [ ! -d "$old" ] || mv "$old" "$PM_DIR"
        fail 'could not install PortMaster to the ROMS partition'
    fi
    chmod -R u+rwX,go+rX "$PM_DIR"
    echo "Installed PortMaster version: $(cat "$PM_DIR/version" 2>/dev/null || echo unknown)"
fi

mkdir -p "$PM_DIR/autoinstall" "$PORTS_DIR/autoinstall" "$PM_DIR/libs" "$PM_DIR/runtimes"
chmod u+x "$PM_DIR/PortMaster.sh"
cd "$PM_DIR"
# Do not inherit RG OS menu's SDL dummy driver into the standalone PortMaster GUI.
unset SDL_VIDEODRIVER RGOS_VIDEO RGOS_FB_DBUF RGOS_FB_VSYNC
export HOME="$ROMS_ROOT/saves/ports/PortMaster/home"
export XDG_CONFIG_HOME="$ROMS_ROOT/saves/ports/PortMaster/config"
export XDG_DATA_HOME="$ROMS_ROOT/saves/ports/PortMaster/data"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"
exec bash "$PM_DIR/PortMaster.sh"
