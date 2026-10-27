# RG OS

Debian + the RG OS launcher on top of each handheld's stock ROCKNIX SD image. Only ROCKNIX's `SYSTEM` squashfs is replaced; kernel, device tree(s), bootloader and partition table stay as ROCKNIX shipped them.

| Device | Argument | SoC | Base image | Partition table |
|---|---|---|---|---|
| Anbernic RG34XXSP | `rg34xxsp` | Allwinner H700 | ROCKNIX-H700 (DDR4) | MBR |
| Anbernic RG35XX H | `rg35xxh` | Allwinner H700 | ROCKNIX-H700 (DDR4) | MBR |
| Anbernic RG Rotate | `rg-rotate` | Unisoc T618 | ROCKNIX-T618 | GPT (`uboot` partition) |

## Build

    sudo ./build.sh rg34xxsp        # out/rg34xxsp-rgos.img
    sudo ./build.sh rg35xxh         # out/rg35xxh-rgos.img
    sudo ./build.sh rg-rotate       # out/rg-rotate-rgos.img
    sudo ./build.sh all             # both
    sudo ./build.sh rg34xxsp update # offline update tar (see docs/UPDATES.md)

`sudo ./build.sh` with no device only prints the usage text; nothing is downloaded, mounted or written.

See `docs/BUILD-WORKFLOW.md` (GitHub Actions and build options) and `docs/UPDATES.md`.
