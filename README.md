# RG OS

RG OS is an open source Linux emulation distribution for retro handhelds built on top of Debian using the ROCKNIX kernel and Device Trees.

# Supported Devices
| Device | Argument | SoC | Base image | Partition table |
|---|---|---|---|---|
| Anbernic RG34XXSP | `rg34xxsp` | Allwinner H700 | ROCKNIX-H700 (DDR4) | MBR |
| Anbernic RG35XX H | `rg35xxh` | Allwinner H700 | ROCKNIX-H700 (DDR4) | MBR |
| Anbernic RG Rotate | `rg-rotate` | Unisoc T618 | ROCKNIX-T618 | GPT (`uboot` partition) |

## Build Image

    sudo ./build.sh rg34xxsp        # out/rg34xxsp-rgos.img
    sudo ./build.sh rg35xxh         # out/rg35xxh-rgos.img
    sudo ./build.sh rg-rotate       # out/rg-rotate-rgos.img
    sudo ./build.sh all             # builds all images
    

`sudo ./build.sh` with no device only prints the usage text; nothing is downloaded, mounted or written.

See `docs/BUILD-WORKFLOW.md` (GitHub Actions and build options) and `docs/UPDATES.md`.

## Build Update

    sudo ./build.sh rg34xxsp update         #out/rg34xxsp-update.tar
    sudo ./build.sh rg35xxh update          #out/rg35xxh-update.tar
    sudo ./build.sh rg-rotate update        #out/rg-rotate-update.tar

# Join the Discord!
By joining our [Discord Server](https://discord.gg/PmQeRvpGa), you can help grow our community as well as chat with us, get support and much more.

# Credits & Thanks
- Thanks to MuOS for being the entire inspiration behind this CFW.
- Credits to Noxwell for the ROCKNIX kernel and Device Trees for the Anbernic RG ROTATE.
- Credits to ROCKNIX for the kernel and Device Trees.
