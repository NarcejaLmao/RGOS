#!/bin/bash
# RG OS Tools > PortMaster: install (first run) and start the official PortMaster app.
#
#   * The app lives on the ROMS card (roms/ports/PortMaster), so it survives firmware updates.
#   * rgos-session runs this like any port: tty1 is in graphics mode and stdout goes to a log, so every failure is also
#     printed on screen (fail) - otherwise a problem just looks like the menu coming back.
#   * Env overrides (used by tests): ROMS_ROOT PORTS_DIR RGOS_ROMS_LINK RGOS_TTY RGOS_FAIL_PAUSE PM_API PM_CHANNEL
set -Eeuo pipefail

ROMS_ROOT="${ROMS_ROOT:-/media/roms}"
PORTS_DIR="${PORTS_DIR:-$ROMS_ROOT/roms/ports}"
PM_DIR="$PORTS_DIR/PortMaster"
SAVE_DIR="${RGOS_PORT_SAVE_DIR:-$ROMS_ROOT/saves/ports/PortMaster}"
LOG="$SAVE_DIR/portmaster.log"
ROMS_LINK="${RGOS_ROMS_LINK:-/roms}"          # PortMaster and many ports expect /roms/ports
TTY="${RGOS_TTY:-/dev/tty1}"
FAIL_PAUSE="${RGOS_FAIL_PAUSE:-6}"
PM_CHANNEL="${PM_CHANNEL:-stable}"            # stable | beta | alpha | any (GitHub's "latest" is often an alpha)
PM_API="${PM_API:-https://api.github.com/repos/PortsMaster/PortMaster-GUI/releases?per_page=30}"

mkdir -p "$SAVE_DIR" "$PORTS_DIR"
exec > >(tee -a "$LOG") 2>&1

fail() {
    trap - ERR
    echo; echo "PortMaster could not start: $*"; echo "Log: $LOG"
    # tty1 is in graphics mode while a port runs: switch it back to text so the message is visible
    python3 -c 'import sys; sys.path.insert(0, "/opt/rgos"); import fbout; fbout.set_graphics(False)' 2>/dev/null || true
    printf '\nPortMaster could not start:\n%s\n\nLog: %s\n' "$*" "$LOG" > "$TTY" 2>/dev/null || true
    sleep "$FAIL_PAUSE"
    exit 1
}
work=""                                       # temp dir of install_app, removed on exit
trap 'fail "unexpected error (line $LINENO: $BASH_COMMAND)"' ERR

check_tools() {
    [ -d "$ROMS_ROOT/roms" ] || fail "ROMS partition is not mounted at $ROMS_ROOT"
    local t
    for t in curl unzip python3; do command -v "$t" >/dev/null || fail "$t is not installed"; done
}

# /roms -> <ROMS card>/roms, the path PortMaster and most ports hard-code.
link_roms() {
    if [ -L "$ROMS_LINK" ] && [ ! -e "$ROMS_LINK" ]; then rm -f "$ROMS_LINK"; fi      # dangling link
    [ -e "$ROMS_LINK" ] || ln -s "$ROMS_ROOT/roms" "$ROMS_LINK" 2>/dev/null || true
    [ -d "$ROMS_LINK" ] || fail "could not provide the conventional $ROMS_LINK path"
    [ "$(readlink -f "$ROMS_LINK")" = "$(readlink -f "$ROMS_ROOT/roms")" ] \
        || fail "$ROMS_LINK exists but is not the ROMS card ($(readlink -f "$ROMS_LINK")); PortMaster would not find its files"
}

# Print the PortMaster.zip URL of the newest release in the wanted channel (falls back to the newest of any channel).
pick_release_url() {
    python3 - "$1" "$PM_CHANNEL" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
want = sys.argv[2].lower()
if isinstance(data, dict):                  # an error object (rate limit ...) or a single release
    if "message" in data and "assets" not in data:
        print("GitHub said: %s" % data["message"], file=sys.stderr)
    data = [data]

def zip_url(rel):
    if rel.get("draft"):
        return ""
    for asset in rel.get("assets", []):
        if asset.get("name") == "PortMaster.zip":
            return asset.get("browser_download_url", "")
    return ""

def channel(rel):                           # body line: "Release: 2026.09.13-0343 (alpha)"
    m = re.search(r"\((stable|beta|alpha)\)", "%s\n%s" % (rel.get("name") or "", rel.get("body") or ""), re.I)
    return m.group(1).lower() if m else "unknown"

releases = [r for r in data if zip_url(r)]  # newest first
match = [r for r in releases if want == "any" or channel(r) == want]
if not match and releases:
    print("no %s release found, using the newest" % want, file=sys.stderr)
pick = (match or releases or [None])[0]
if pick:
    print("PortMaster release: %s (%s)" % (pick.get("tag_name"), channel(pick)), file=sys.stderr)
    print(zip_url(pick))
PY
}

install_app() {
    local url keep old
    work=$(mktemp -d /tmp/rgos-portmaster.XXXXXX)
    trap '[ -z "$work" ] || rm -rf "$work"' EXIT
    echo "Downloading PortMaster..."
    curl -fLsS --retry 2 --connect-timeout 15 "$PM_API" -o "$work/releases.json" \
        || fail 'cannot reach GitHub; connect Wi-Fi and retry'
    url=$(pick_release_url "$work/releases.json") || fail 'could not read the PortMaster release list'
    [ -n "$url" ] || fail 'official release list did not contain PortMaster.zip'
    curl -fLsS --retry 2 --connect-timeout 15 "$url" -o "$work/PortMaster.zip" \
        || fail 'PortMaster download failed; check Wi-Fi and retry'
    unzip -tq "$work/PortMaster.zip" >/dev/null || fail 'downloaded PortMaster ZIP is invalid'
    unzip -q "$work/PortMaster.zip" -d "$work/extract" || fail 'could not extract PortMaster package'
    [ -f "$work/extract/PortMaster/PortMaster.sh" ] || fail 'official ZIP has an unexpected directory layout'

    # keep what the user or PortMaster added to a previous install
    for keep in autoinstall libs runtimes; do
        if [ -d "$PM_DIR/$keep" ]; then
            mkdir -p "$work/extract/PortMaster/$keep"
            cp -an "$PM_DIR/$keep/." "$work/extract/PortMaster/$keep/" 2>/dev/null || true
        fi
    done
    # replace only the application folder - never ROMs, saves or states
    old="$PORTS_DIR/.PortMaster-old.$$"
    rm -rf "$old"
    [ ! -d "$PM_DIR" ] || mv "$PM_DIR" "$old"
    if mv "$work/extract/PortMaster" "$PM_DIR"; then
        rm -rf "$old"
    else
        [ ! -d "$old" ] || mv "$old" "$PM_DIR"
        fail 'could not install PortMaster to the ROMS partition'
    fi
    echo "Installed PortMaster version: $(cat "$PM_DIR/version" 2>/dev/null || echo unknown)"
}

run_app() {
    local start=$SECONDS rc
    mkdir -p "$PM_DIR/autoinstall" "$PORTS_DIR/autoinstall" "$PM_DIR/libs" "$PM_DIR/runtimes"
    chmod -R u+rwX,go+rX "$PM_DIR" 2>/dev/null || true       # exFAT/FAT permissions come from the mount: refusal is fine
    chmod u+x "$PM_DIR/PortMaster.sh" 2>/dev/null || true
    cd "$PM_DIR"
    unset SDL_VIDEODRIVER RGOS_VIDEO RGOS_FB_DBUF RGOS_FB_VSYNC    # the menu's dummy/fb settings must not reach the app
    export HOME="$SAVE_DIR/home" XDG_CONFIG_HOME="$SAVE_DIR/config" XDG_DATA_HOME="$SAVE_DIR/data"
    mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"
    # audio straight to ALSA, like rgos-splash and RetroArch (OpenAL otherwise probes PipeWire first)
    [ -r /etc/rgos/alsoft.conf ] && export ALSOFT_CONF="${ALSOFT_CONF:-/etc/rgos/alsoft.conf}"
    export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-alsa}"

    trap - ERR
    set +e
    bash "$PM_DIR/PortMaster.sh"          # not exec: an immediate crash is reported below
    rc=$?
    set -e
    if [ "$rc" -ne 0 ] && [ $((SECONDS - start)) -lt 15 ]; then
        fail "the PortMaster app exited with code $rc right after starting. If this repeats, delete the folder roms/ports/PortMaster on the card (ports and saves are kept) and start it again to reinstall."
    fi
    exit "$rc"
}

check_tools
link_roms
[ -f "$PM_DIR/PortMaster.sh" ] || install_app
run_app
