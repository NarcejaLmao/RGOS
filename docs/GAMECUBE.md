# Nintendo GameCube (RG Rotate only)

GameCube runs through RetroArch with the **Dolphin libretro** core (`dolphin_libretro.so`). It is built from source (`GC_REPO`, default `https://github.com/libretro/dolphin.git`) **only when building `rg-rotate`**. The `rg34xxsp` and `rg35xxh` builds never compile or ship it, and `menu.py` hides the system on any other device.

**GameCube only, no Wii.** The Dolphin core can technically run both, so there is no Wii menu entry, no `roms/wii` folder, and `.wbfs`/`.wad` are not accepted. Wii games placed in `roms/gc` are not supported (`.iso` is shared by both consoles, so keep Wii dumps out of that folder).

* ROMs: `<ROMS>/roms/gc/*.gcm`, `.iso`, `.gcz`, `.rvz`, `.ciso`, `.m3u`, `.dol`, `.elf`.
* Saves: `<ROMS>/saves/gc/`, states: `<ROMS>/states/gc/`.
* Dolphin's `Sys` data folder is shipped in the image and copied once to `<ROMS>/bios/dolphin-emu/Sys` on the first GameCube launch.
* Optional GameCube BIOS (boot logo): `IPL.bin` in `<ROMS>/bios/dolphin-emu/Sys/GC/<USA|EUR|JAP>/`.

## Build options

    sudo ./build.sh rg-rotate          # includes the GameCube core
    GC_REPO=none sudo -E ./build.sh rg-rotate   # skip it (faster build)

A prebuilt aarch64 `dolphin_libretro.so` in `cores/` is used instead of building (and is deleted again for every device except `rg-rotate`). In GitHub Actions use the *build_gc* checkbox.

The build is best-effort, like the other source-built cores: if it fails, a `!!` line is printed and the image is built without GameCube. It is slow under qemu emulation (plan on a long run).


## Runtime dependency fix

The Dolphin core may fail to load with `liblzo2.so.2: cannot open shared object file` when a prebuilt `dolphin_libretro.so` is used. RG OS now includes Debian's `liblzo2-2` runtime package in the base image so this dependency is present even for prebuilt cores. Rebuild the image or install `liblzo2-2` on an existing Debian-based RG OS image.
