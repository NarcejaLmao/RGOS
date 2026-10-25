"""Startup splash video for the RG OS menu.

The video is an MJPEG stream: JPEG frames written one after the other in a single file (`splash.mjpg`), at a fixed frame rate
(`fps` in the `.json` next to it, default 24). Decoding is done by pygame/SDL_image, so no extra package (ffmpeg, mpv, ...) is
needed on the device. Make your own with `tools/make-splash` on a PC. An optional `.wav` with the same base name (or the
`sound` entry of the `.json`) is played alongside.

Search order (first hit wins):
  1. <ROMS>/splash/splash.mjpg                   put your own file on the card, survives firmware updates
  2. /etc/rgos/splash/splash-<device>.mjpg       the video shipped with RG OS, sized for the device's canvas
  3. /etc/rgos/splash/splash.mjpg

This module has no pygame import: play() takes the decoder and the screen callbacks as arguments, so the timing logic is
testable without a display.
"""
import json, os, time

SOI = b"\xff\xd8\xff"
DEFAULT_FPS = 24
MODES = ("on", "silent", "off")            # Settings > Startup splash: video + sound, video only, nothing


def split_mjpeg(data):
    """Return [(start, end), ...] of every JPEG frame in a concatenated-JPEG byte string.

    A frame runs from one SOI marker to the next. FF bytes inside JPEG entropy data are always stuffed (FF 00), so FF D8 FF only
    appears at real frame starts when the encoder writes no embedded thumbnails (ffmpeg and PIL do not)."""
    starts, pos = [], 0
    while True:
        i = data.find(SOI, pos)
        if i < 0:
            break
        starts.append(i)
        pos = i + 3
    return [(s, starts[k + 1] if k + 1 < len(starts) else len(data)) for k, s in enumerate(starts)]


def find_splash(device, roms_root, etc_dir="/etc/rgos/splash"):
    """Path of the splash video to play, or None."""
    for p in (os.path.join(roms_root, "splash", "splash.mjpg"),
              os.path.join(etc_dir, "splash-%s.mjpg" % device),
              os.path.join(etc_dir, "splash.mjpg")):
        try:
            if os.path.getsize(p) > 0:
                return p
        except OSError:
            pass
    return None


def read_conf(path):
    """{"fps": float, "sound": path or None} for a splash video (sidecar .json optional, bad values fall back to defaults)."""
    base = os.path.splitext(path)[0]
    conf = {}
    try:
        with open(base + ".json") as f:
            conf = json.load(f)
    except Exception:
        pass
    try:
        fps = float(conf.get("fps", DEFAULT_FPS))
        if not 1 <= fps <= 60:
            raise ValueError
    except Exception:
        fps = float(DEFAULT_FPS)
    snd = conf.get("sound")
    snd = os.path.join(os.path.dirname(path), snd) if isinstance(snd, str) and snd else base + ".wav"
    return {"fps": fps, "sound": snd if os.path.isfile(snd) else None}


def mode_of(state):
    m = state.get("splash", "on") if isinstance(state, dict) else "on"
    return m if m in MODES else "on"


def play(data, frames, fps, decode, show, skip, now=time.time, sleep=time.sleep, max_wait=0.02):
    """Play frames[i] of data at fps. decode(bytes) -> surface, show(surface) puts it on the screen, skip() -> True stops early.

    The frame shown follows the clock, so a slow device drops frames instead of running late (the sound stays in sync).
    Returns the number of frames shown; a frame that fails to decode is skipped, 5 failures in a row end the video."""
    if not frames:
        return 0
    t0, last, shown, bad = now(), -1, 0, 0
    while True:
        if skip():
            break
        i = int((now() - t0) * fps)
        if i >= len(frames):
            break
        if i != last:
            last = i
            s, e = frames[i]
            try:
                show(decode(data[s:e]))
                shown += 1
                bad = 0
            except Exception:
                bad += 1
                if bad >= 5:
                    break
        else:
            sleep(min(max_wait, max(0.0, (t0 + (i + 1) / fps) - now())))
    return shown
