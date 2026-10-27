# RG OS

Debian + the RG OS launcher on top of each handheld's stock ROCKNIX SD image. Only ROCKNIX's `SYSTEM` squashfs is replaced; kernel, device tree(s), bootloader and partition table stay as ROCKNIX shipped them.

| Device | Argument | SoC | Base image | Partition table |
|---|---|---|---|---|
| Anbernic RG34XXSP | `rg34xxsp` | Allwinner H700 | ROCKNIX-H700 (DDR4) | MBR |
| Anbernic RG Rotate | `rg-rotate` | Unisoc T618 | ROCKNIX-T618 | GPT (`uboot` partition) |

## Build

    sudo ./build.sh rg34xxsp        # out/rg34xxsp-rgos.img
    sudo ./build.sh rg-rotate       # out/rg-rotate-rgos.img
    sudo ./build.sh all             # both
    sudo ./build.sh rg34xxsp update # offline update tar (see docs/UPDATES.md)

`sudo ./build.sh` with no device only prints the usage text; nothing is downloaded, mounted or written.

See `docs/BUILD-WORKFLOW.md` (GitHub Actions and build options) and `docs/UPDATES.md`.

## RG34XXSP notes

* No SPL flashing: the H700 boots from the SD card as ROCKNIX does. Just write `out/rg34xxsp-rgos.img` to the card.
* DDR4 is the default image (the RG34XXSP has LPDDR4). `H700_RAM=DDR3 sudo -E ./build.sh rg34xxsp` builds on the DDR3 ROCKNIX image instead.
* v2-panel units: `RG34XXSP_DTB=sun50i-h700-anbernic-rg34xx-sp-v2-panel sudo -E ./build.sh rg34xxsp`, (`device_trees/` is not on the card, so the dtb is chosen at build time).
* Menu canvas is 720x480; the startup splash is `splash/splash-rg34xxsp.mjpg`.
* Analog sticks: the input hub publishes both sticks (left = axes 0/1, right = 2/3) and RetroArch gets matching binds. This is RG34XXSP-only (`/etc/rgos/input-rg34xxsp.json`, `/etc/rgos/retroarch-rg34xxsp.cfg`); the RG Rotate image does not contain these files. The menu is still driven by the D-pad.
