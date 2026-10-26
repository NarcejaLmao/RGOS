# NES / Famicom (RetroArch + FCEUmm core, aarch64)

RG OS plays NES through RetroArch with the **FCEUmm libretro core**, cross-built next to mGBA. It is wired exactly like the GBA core:

| Piece | Where |
|---|---|
| FCEUmm package (`fceumm_libretro.so` -> `/usr/lib/libretro`, plus its `.info`) | `buildroot-external/package/rgos-libretro-fceumm/` |
| Enabled in | `buildroot-external/configs/rgos_rg34xxsp_defconfig` (`BR2_PACKAGE_RGOS_LIBRETRO_FCEUMM=y`) |
| Core options | `overlay/etc/rgos/retroarch-core-options.cfg` (`fceumm_*`) |
| Menu system `nes` (default core `fceumm`, alternatives nestopia/quicknes/mesen) | `overlay/opt/rgos/menu.py` (already there) |

* ROMs: `<ROMS>/roms/nes/*.nes` (also `.unf`, `.unif`, `.zip`). Famicom Disk System: `.fds` plus `disksys.rom` in `<ROMS>/bios/`.
* Saves: `<ROMS>/saves/nes/`, states: `<ROMS>/states/nes/`. Hotkeys are the same as for GBA (see `docs/GBA.md`).
* `BR2_PACKAGE_RGOS_LIBRETRO_FCEUMM_VERSION` defaults to `master`; pin a commit and add a `.hash` file once a build works.
* RG Rotate (Debian image): Nestopia is the default NES core, installed from the `libretro-nestopia` package (`EMU_PKGS` in `boards/other/rg-rotate/build.sh`);
  the menu offers whichever NES core is installed.
