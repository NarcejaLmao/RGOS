# Game Boy Advance (RetroArch + mGBA core, aarch64)

RG OS plays GBA through RetroArch with the **mGBA libretro core**, cross-built for the RG34XX-SP's Cortex-A53.
mGBA also runs GB and GBC, so those systems in the menu work as well (the menu still prefers `gambatte` for them
when it is installed; it is not part of this build).

## What is in the build

| Piece | Where |
|---|---|
| mGBA core package (`mgba_libretro.so` -> `/usr/lib/libretro`, plus its `.info`) | `buildroot-external/package/rgos-libretro-mgba/` |
| RetroArch package (built from source: Buildroot has none) | `buildroot-external/package/rgos-retroarch/` |
| Mesa (Panfrost, KMSRO, GBM, EGL/GLES) + SDL2 selection | `buildroot-external/configs/rgos_rg34xxsp_defconfig` |
| RetroArch base config (video, audio, input, hotkeys) | `overlay/etc/rgos/retroarch.cfg` |
| mGBA core options | `overlay/etc/rgos/retroarch-core-options.cfg` |
| Launcher: ROM -> system -> core -> RetroArch | `overlay/opt/rgos/rgos-launch` |
| Session hook (runs the launcher, loads GPU/sound modules) | `overlay/opt/rgos/rgos-session` |

Flow: menu writes the ROM path to `/run/rgos/launch` and exits 10 -> `rgos-session` runs `rgos-launch` -> it picks the
core (Settings > Emulators, else the default `mgba`), writes `/run/rgos/retroarch-run.cfg` and starts RetroArch ->
when RetroArch quits the session starts the menu again.

## Using it

* ROMs: `<ROMS>/roms/gba/*.gba` (also `.zip`). Optional BIOS: `<ROMS>/bios/gba_bios.bin` (mGBA has a built-in substitute).
* Saves: `<ROMS>/saves/gba/` (battery saves are flushed every 10 s), states: `<ROMS>/states/gba/`.
* Hold **MENU**, then: **START** quit, **SELECT** RetroArch menu, **R** save state, **L** load state, **X** fast-forward, **Y** pause.
* The 720x480 panel is exactly 3x the GBA's 240x160, so the image is integer-scaled with no filtering.
* Your own RetroArch overrides: `<ROMS>/config/retroarch.cfg` (appended last, wins over everything).

## Build notes

* `BR2_PACKAGE_RGOS_LIBRETRO_MGBA_VERSION` defaults to `master`. Pin a commit once you have a working build and add
  a `rgos-libretro-mgba.hash` (`make rgos-libretro-mgba-source` downloads it; `sha256sum` the tarball).
* The initramfs grows by roughly the size of Mesa + RetroArch (tens of MB). The device has to hold it in RAM.
* `RGOS_MODULE_FILTER` now also ships `drivers/gpu`, `drivers/devfreq` and `sound` modules (only matters if the ROCKNIX
  kernel builds Panfrost / the audio codec as modules rather than into the kernel).

## If a game does not start

The menu comes back; the reason is on the console for a few seconds and in `rgos-logs.txt` on the FAT boot partition
(sections `rgos-launch.log` and `rgos-retroarch.log`, plus a `/dev/dri` listing). Typical causes:

* `no DRM device` / EGL errors -> Panfrost is not bound (`/dev/dri/renderD*` missing): kernel or module problem.
* black screen but audio -> wrong KMS device; try `video_context_driver`/`video_driver` overrides in `<ROMS>/config/retroarch.cfg`.
* no buttons in game -> `input_joypad_driver = "linuxraw"` needs the `joydev` module and `/dev/input/js0`.
* no sound -> check `audio_device` in `/run/rgos/retroarch-run.cfg` against `/proc/asound/cards`.

## Build machine memory (WSL)

Building LLVM (needed by Mesa's Panfrost driver) uses ~4 GiB of RAM per parallel job. `build.sh` picks the job count
from the free RAM; if you still see `g++: Killed signal terminated program cc1plus`, the OOM killer ran. Give WSL more
memory and swap in `%UserProfile%\.wslconfig`, then run `wsl --shutdown`:

    [wsl2]
    memory=12GB
    swap=16GB

and/or build with `RGOS_JOBS=2 ./build.sh build`. Re-running continues where it stopped.
