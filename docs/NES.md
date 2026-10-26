# NES / Famicom (RetroArch + Nestopia core)

On the RG Rotate and RG34XXSP, NES runs through RetroArch with the **Nestopia** libretro core, installed from the Debian `libretro-nestopia` package (`EMU_PKGS` in `build.sh`). The menu offers whichever NES core is installed (default Nestopia; alternatives fceumm/quicknes/mesen if present).

* ROMs: `<ROMS>/roms/nes/*.nes` (also `.unf`, `.unif`, `.zip`). Famicom Disk System: `.fds` plus `disksys.rom` in `<ROMS>/bios/`.
* Saves: `<ROMS>/saves/nes/`, states: `<ROMS>/states/nes/`.
* Your own RetroArch overrides: `<ROMS>/config/retroarch.cfg` (appended last, wins over everything).
