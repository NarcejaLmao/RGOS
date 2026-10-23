#!/bin/bash
# RG OS Tools > PortMaster: install and run the official PortMaster application.
# The application is stored on the ROMS card so it survives firmware updates.
#
# rgos-session runs this like any other port, i.e. with tty1 in graphics mode and all output going to a log file. A
# failure therefore used to look like "the menu came straight back". Every failure below is now also shown on screen.
set -Eeuo pipefail

ROMS_ROOT="${ROMS_ROOT:-/media/roms}"
PORTS_DIR="${PORTS_DIR:-$ROMS_ROOT/roms/ports}"
PM_DIR="$PORTS_DIR/PortMaster"
LOG_DIR="${RGOS_PORT_SAVE_DIR:-$ROMS_ROOT/saves/ports/PortMaster}"
LOG="$LOG_DIR/portmaster.log"
ROMS_LINK="${RGOS_ROMS_LINK:-/roms}"          # PortMaster and many ports expect /roms/ports
TTY="${RGOS_TTY:-/dev/tty1}"
FAIL_PAUSE="${RGOS_FAIL_PAUSE:-6}"
# Release channel to install: stable (default) | beta | alpha | any. GitHub's "latest" release is whatever was
# published last, which is often an alpha build, so it is not used.
PM_CHANNEL="${PM_CHANNEL:-stable}"
PM_API="${PM_API:-https://api.github.com/repos/PortsMaster/PortMaster-GUI/releases?per_page=30}"
mkdir -p "$LOG_DIR" "$PORTS_DIR"
exec > >(tee -a "$LOG") 2>&1

fail() {
    trap - ERR
    echo
    echo "PortMaster could not start: $*"
    echo "Log: $LOG"
    # tty1 is in graphics mode while a port runs, so plain text would never be seen: switch it back to text first.
    python3 -c 'import sys; sys.path.insert(0, "/opt/rgos"); import fbout; fbout.set_graphics(False)' 2>/dev/null || true
    printf '\nPortMaster could not start:\n%s\n\nLog: %s\n' "$*" "$LOG" > "$TTY" 2>/dev/null || true
    sleep "$FAIL_PAUSE"
    exit 1
}
# A command failing under `set -e` used to end the script silently; report it instead.
trap 'fail "unexpected error (line $LINENO: $BASH_COMMAND)"' ERR

[ -d "$ROMS_ROOT/roms" ] || fail "ROMS partition is not mounted at $ROMS_ROOT"
command -v curl >/dev/null || fail 'curl is not installed'
command -v unzip >/dev/null || fail 'unzip is not installed'
command -v python3 >/dev/null || fail 'python3 is not installed'

# PortMaster and many port scripts expect /roms/ports. RG OS mounts the card
# at /media/roms and stores the ROM tree under /media/roms/roms.
if [ -L "$ROMS_LINK" ] && [ ! -e "$ROMS_LINK" ]; then
    rm -f "$ROMS_LINK"                         # dangling link: -e is false for it, so ln -s below would be skipped
fi
if [ ! -e "$ROMS_LINK" ]; then
    ln -s "$ROMS_ROOT/roms" "$ROMS_LINK" 2>/dev/null || true
fi
[ -d "$ROMS_LINK" ] || fail "could not provide the conventional $ROMS_LINK path"
[ "$(readlink -f "$ROMS_LINK")" = "$(readlink -f "$ROMS_ROOT/roms")" ] \
    || fail "$ROMS_LINK exists but is not the ROMS card ($(readlink -f "$ROMS_LINK")); PortMaster would not find its files"

if [ ! -x "$PM_DIR/PortMaster.sh" ]; then
    work=$(mktemp -d /tmp/rgos-portmaster.XXXXXX)
    trap 'rm -rf "$work"' EXIT
    echo 'Downloading the official PortMaster release metadata...'
    curl -fLsS --retry 2 --connect-timeout 15 "$PM_API" -o "$work/releases.json" \
        || fail 'cannot reach GitHub; connect Wi-Fi and retry'
    url=$(python3 - "$work/releases.json" "$PM_CHANNEL" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding='utf-8') as f:
    data = json.load(f)
want = sys.argv[2].lower()
if isinstance(data, dict):          # an error object (rate limit ...) or a single release
    if 'message' in data and 'assets' not in data:
        print('GitHub said: ' + str(data['message']), file=sys.stderr)
    data = [data]

def zip_url(rel):
    if rel.get('draft'):
        return ''
    for asset in rel.get('assets', []):
        if asset.get('name') == 'PortMaster.zip':
            return asset.get('browser_download_url', '')
    return ''

def channel(rel):
    # releases carry a line like "Release: 2026.09.13-0343 (alpha)" (stable | beta | alpha)
    text = '%s\n%s' % (rel.get('name') or '', rel.get('body') or '')
    m = re.search(r'\((stable|beta|alpha)\)', text, re.I)
    return m.group(1).lower() if m else 'unknown'

picked = None
for rel in data:                    # newest first
    if zip_url(rel) and (want == 'any' or channel(rel) == want):
        picked = rel
        break
if picked is None:
    for rel in data:
        if zip_url(rel):
            picked = rel
            print('no %s release found, using %s (%s)' % (want, rel.get('tag_name'), channel(rel)), file=sys.stderr)
            break
if picked is not None:
    print('PortMaster release: %s (%s)' % (picked.get('tag_name'), channel(picked)), file=sys.stderr)
    print(zip_url(picked))
PY
    ) || fail 'could not read the PortMaster release list'
    [ -n "$url" ] || fail 'official release list did not contain PortMaster.zip'
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
    # exFAT/FAT cannot change permissions (they come from the mount's umask); a refused chmod must not abort the launch.
    chmod -R u+rwX,go+rX "$PM_DIR" 2>/dev/null || true
    echo "Installed PortMaster version: $(cat "$PM_DIR/version" 2>/dev/null || echo unknown)"
fi

mkdir -p "$PM_DIR/autoinstall" "$PORTS_DIR/autoinstall" "$PM_DIR/libs" "$PM_DIR/runtimes"
chmod u+x "$PM_DIR/PortMaster.sh" 2>/dev/null || true
cd "$PM_DIR"
# Do not inherit RG OS menu's SDL dummy driver into the standalone PortMaster GUI.
unset SDL_VIDEODRIVER RGOS_VIDEO RGOS_FB_DBUF RGOS_FB_VSYNC
export HOME="$ROMS_ROOT/saves/ports/PortMaster/home"
export XDG_CONFIG_HOME="$ROMS_ROOT/saves/ports/PortMaster/config"
export XDG_DATA_HOME="$ROMS_ROOT/saves/ports/PortMaster/data"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"

# Not `exec`: when the GUI dies right after starting, say so on screen (and in the log) instead of silently going back.
trap - ERR
start=$SECONDS
set +e
bash "$PM_DIR/PortMaster.sh"
rc=$?
set -e
if [ "$rc" -ne 0 ] && [ $((SECONDS - start)) -lt 15 ]; then
    fail "the PortMaster app exited with code $rc right after starting. If this repeats, delete the folder roms/ports/PortMaster on the card (ports and saves are kept) and start it again to reinstall."
fi
exit "$rc"
