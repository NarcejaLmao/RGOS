# RG OS Third-Party Notices

RG OS's own code is MIT-licensed (see `LICENSE`). Everything below is
separate software with its own license. Licenses are listed per component
as best known; the authoritative text is the one shipped with each package
or in its upstream repository. Debian packages carry theirs in
`/usr/share/doc/<package>/copyright` inside the image.

## 1. Base system (downloaded at build time, not stored in this repo)

| Component | Role in RG OS | License |
|---|---|---|
| ROCKNIX distribution image | Stock boot partition: bootloader/SPL area, `KERNEL`, device trees, kernel modules, firmware. Only `SYSTEM` is replaced. | GPL-2.0 (distribution); see per-file licenses below |
| Linux kernel and modules (ROCKNIX build) | Kernel for all three devices | GPL-2.0-only with the Linux syscall note |
| Device tree blobs (`*.dtb`) | Hardware description | GPL-2.0 (some dual-licensed with BSD/MIT) |
| U-Boot (in the stock image) | Bootloader | GPL-2.0-or-later |
| linux-firmware and vendor blobs copied from stock `SYSTEM` (`/lib/firmware`; Wi-Fi, Bluetooth, GPU, audio DSP, Unisoc WCN) | Hardware firmware | Proprietary, redistributable under each vendor's terms; see the `WHENCE` and `LICENCE.*` files in linux-firmware. Not open source. |
| Debian GNU/Linux 13 "trixie" arm64 (`main`, `non-free`) | Root filesystem | Each package under its own license (below). |

## 2. Debian packages installed in the image

| Package | License |
|---|---|
| busybox-static | GPL-2.0-only |
| kmod | GPL-2.0-or-later / LGPL-2.1-or-later |
| python3 | PSF-2.0 |
| python3-pygame | LGPL-2.1-or-later |
| SDL2 (libsdl2-2.0-0) | zlib |
| Mesa (libgl1-mesa-dri, libegl-mesa0, libgbm1, libgles2, libegl1) | MIT and compatible permissive licenses |
| fonts-dejavu-core | Bitstream Vera Fonts license (free, with DejaVu public-domain changes) |
| e2fsprogs | GPL-2.0 / LGPL-2.0 (library parts) |
| exfatprogs | GPL-2.0-only |
| fdisk (util-linux) | GPL-2.0-or-later |
| gdisk (sgdisk) | GPL-2.0-or-later |
| iproute2 | GPL-2.0-or-later |
| iw | ISC |
| wpasupplicant | BSD-3-Clause |
| rfkill | ISC / GPL-2.0 (see package) |
| alsa-utils | GPL-2.0-or-later |
| bluez | GPL-2.0-or-later / LGPL-2.1-or-later |
| dbus | AFL-2.1 or GPL-2.0-or-later |
| dropbear-bin | MIT-style (Matt Johnston) with portions under BSD and public domain |
| openssh-sftp-server | BSD-style (OpenSSH) |
| liblzo2-2 | GPL-2.0-or-later |
| retroarch | GPL-3.0-or-later |
| libretro-core-info | MIT |

Anything pulled in as a dependency of the packages above is covered by its
own Debian copyright file.

## 3. libretro emulator cores

| Core | Source | License |
|---|---|---|
| Gambatte (Game Boy / Color) | Debian `libretro-gambatte` | GPL-2.0-only |
| mGBA (Game Boy Advance) | Debian `libretro-mgba` | MPL-2.0 |
| Nestopia (NES / Famicom) | Debian `libretro-nestopia` | GPL-2.0-or-later |
| Snes9x (SNES) | Debian `libretro-snes9x` (non-free) | **Snes9x license: non-commercial only.** Not an open-source license. May not be sold or bundled in a commercial product. |
| Beetle WonderSwan | Debian `libretro-beetle-wswan` | GPL-2.0-only |
| Beetle PSX (fallback) | Debian `libretro-beetle-psx` | GPL-2.0-or-later |
| Beetle NGP (`mednafen_ngp`) | built from `libretro/beetle-ngp-libretro` | GPL-2.0-only |
| PCSX ReARMed (PlayStation) | built from `libretro/pcsx_rearmed` | GPL-2.0-or-later |
| Genesis Plus GX (Mega Drive, Master System, Game Gear, SG-1000, Sega CD) | Debian `libretro-genesisplusgx` or built from `libretro/Genesis-Plus-GX` | **Genesis Plus GX license: non-commercial only.** Not an open-source license. Contains code under other terms (see the upstream `LICENSE.txt`). |
| Dolphin libretro (GameCube, RG Rotate only) | built from `libretro/dolphin` | GPL-2.0-or-later |

Non-commercial cores (Snes9x, Genesis Plus GX) mean that **an RG OS image
that includes them must not be sold or distributed for commercial gain.**

## 4. Files stored in this repository

### spl-flasher/ (RG Rotate only)

| File | What it is | License |
|---|---|---|
| `spd_dump-linux`, `spd_dump.exe` | `spd_dump` from Ilya Kurdyukov's *spreadtrum_flash* project | GPL-3.0. Source: <https://github.com/ilyakurdyukov/spreadtrum_flash> |
| `libusb-1.0.dll` | libusb | LGPL-2.1-or-later. Source: <https://libusb.info>. `spd_dump-linux` is statically linked against libusb. |
| `fdl1-dl.bin`, `fdl2-dl.bin` | Unisoc download-mode loaders | **Proprietary (Unisoc).** Not open source. See section 6. |
| `spl.img` | Modified Unisoc SPL | **Proprietary / unconfirmed.** See section 6. |
| `spl_stock.img` | Stock Unisoc SPL backup | **Proprietary (Unisoc / Anbernic).** See section 6. |
| `*.sh`, `*.bat`, `README.txt` | RG OS flasher scripts | MIT (RG OS) |

Because `spd_dump` is GPL-3.0, anyone who receives these binaries is
entitled to its source code (link above). They are used only as separate
programs run from the scripts, so the MIT-licensed RG OS code is not
affected.

### music/

| File | License |
|---|---|
| `Some Potential.mp3`, by Bolt Action | **Unconfirmed.** See section 6. Not covered by the MIT License. |

### splash/

| File | License |
|---|---|
| `splash-*.mjpg`, `splash-*.json`, `splash.wav` | RG OS original work, MIT (assumed to be created by the project; see section 6). |

## 5. GPL / LGPL source-code offer

RG OS images contain programs licensed under the GPL and LGPL. For each
such program you may obtain the complete corresponding source code:

- **Debian packages:** from the Debian archive (`deb-src` lines for
  `trixie`, or <https://snapshot.debian.org>). Package names are in
  sections 2 and 3.
- **ROCKNIX kernel, U-Boot, device trees and the stock image:** from the
  ROCKNIX release page matching the `ROCKNIX_URL` / `ROCKNIX_H700_VERSION`
  used in `build.sh` (for the RG Rotate, the fork named in `build.sh`).
- **Source-built cores (PCSX ReARMed, Genesis Plus GX fallback, Beetle NGP,
  Dolphin):** from the repositories named in `build.sh` (`PSX_REPO`,
  `GENESIS_REPO`, `NGP_SRC`, `GC_REPO`), at the revision cloned during the
  build.
- **spd_dump and libusb:** from the links in section 4.

If you distribute an RG OS image, you must pass these offers on, or ship
the source yourself, and keep the license texts intact.

## 6. Items the maintainers must confirm before public release

These files were found in the repository, but nothing in them establishes
who may license them or under what terms:

1. **`music/Some Potential.mp3`.** Its tags name "Bolt Action" and the
   "BOLT ACTION - Topic" channel, which is how auto-generated streaming
   channels are labelled. Without a written license or permission from the
   rights holder, this file may not be redistributed. Confirm the license
   (and add its terms and attribution here), or remove the file.
2. **`spl-flasher/fdl1-dl.bin`, `fdl2-dl.bin`, `spl.img`, `spl_stock.img`.**
   The loaders contain Unisoc strings and U-Boot code, and the SPL images
   are bootloader firmware for the Unisoc T618. Confirm where each came
   from and that redistribution is allowed. If `spl.img` was built from
   open-source code, publish that source and state its license here.
3. **Splash animation and sound.** Assumed to be original RG OS work. If
   any part was made with third-party assets, add them here.
4. **Copyright holder.** `LICENSE` names "RG OS contributors". Replace it
   with the real name(s) if you want a specific owner.
