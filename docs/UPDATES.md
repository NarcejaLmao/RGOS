# Offline updates (.tar from the SD card)

RG OS (RG Rotate and RG34XXSP) boots a Debian squashfs (`SYSTEM`) plus a small initramfs from the FAT boot partition. An update replaces files on that partition: `SYSTEM` (and its `.md5`), `rgos-initrd.cpio`, and optionally `KERNEL`, the device tree (`device_trees/ums512-rg-rotate.dtb` on the RG Rotate; `dtb.img` on the RG34XXSP) and `extlinux/extlinux.conf`.

## Build

A device is always required (`./build.sh` alone does nothing); an update only installs on the device it was built for.

    sudo ./build.sh rg34xxsp update                       # pack out/rgos-update-rg34xxsp-<id>.tar
    sudo ./build.sh rg-rotate update                      # pack out/rgos-update-rg-rotate-<id>.tar
    sudo ./build.sh all update                            # both, with the same id
    UPDATE_ID=2026.11-rc1 sudo -E ./build.sh rg34xxsp update   # custom id (default: timestamp)
    UPDATE_EXTLINUX=1 sudo -E ./build.sh rg34xxsp update       # also include extlinux.conf
    UPDATE_DTB=1 sudo -E ./build.sh rg34xxsp update            # also include dtb.img (leave it out if you picked the v2-panel dtb by hand)

## Install

1. Copy the tar into a folder named `update` in the root of the exFAT (or FAT/ext4) ROMS partition (or on the FAT boot partition when the card has no ROMS partition).
2. Boot the device. The console shows the update progress, the files are staged and verified, then it reboots by itself.

Afterwards the tar is renamed to `.applied` (or `.failed`), and `update/rgos-update.log` gets a result line.
The installed build id is stored in `rgos-update.id` on the boot partition; a tar with that id is skipped.

## Safety

* Files are staged as `*.new`, fsynced and SHA-256 checked before anything is replaced.
* Wrong device (`manifest.json` `device` must match `/etc/rgos-device`, i.e. `rg-rotate`, `rg34xxsp` or `rg35xxh`), unknown files, bad paths, damaged tar, too little free space: nothing is changed and the old system boots.
* Replaced files are kept as `*.prev` on the boot partition when there is room. To roll back by hand, put the card in a PC and rename them back.

Manual test on a PC: `rgos-update apply TAR BOOTDIR` (set `RGOS_DEVICE_FILE`, `RGOS_UPDATE_LOG`, `RGOS_UPDATE_TTY` to local paths).

## If an update fails

* The reason is the last line of `update/rgos-update.log` (next to the tar) and is also shown on the console. The tar is renamed `*.failed`; rename it back to `.tar` after fixing the cause to retry.
* `not enough free space`: the new files are staged beside the old ones, so free about the size of the new `SYSTEM`/initramfs on the FAT partition.
* `boot partition is not writable`: the FAT partition was mounted read-only (dirty flag); repair it on a PC.
