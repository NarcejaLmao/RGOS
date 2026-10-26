# Offline updates (.tar from the SD card)

RG OS runs from a RAM initramfs, so an update replaces the files on the FAT boot partition:
`rgos-initrd.gz` (launcher + input/filesystem modules), `rgos.dtb`, the kernel (`KERNEL`/`Image`) and `extlinux/extlinux.conf`.

## Build

    ./build.sh update                      # rebuild rootfs, pack output/rgos-update-<id>.tar
    RGOS_SKIP_BUILDROOT=1 ./build.sh update   # re-pack without running Buildroot
    RGOS_UPDATE_SLIM=1 ./build.sh update      # leave the kernel out (smaller tar)
    RGOS_BUILD_ID=2026.11-rc1 ./build.sh update   # custom id (default: UTC timestamp)

## Install

1. Copy the tar into a folder named `update` in the root of the exFAT (or FAT/ext4) ROMS partition (or `roms/update` on the FAT boot partition when the card has no ROMS partition).
2. Boot the device. The console shows `RG OS update: ...`, the files are staged and verified, then it reboots by itself.

Afterwards the tar is renamed to `.applied` (or `.failed`), and `update/rgos-update.log` gets a result line.
The installed build id is stored in `rgos-update.id` on the boot partition; a tar with that id is skipped.

## Safety

* Files are staged as `*.new`, fsynced and SHA-256 checked before anything is replaced; magic numbers of kernel/DTB/initramfs are checked.
* Wrong device (`manifest.json` `device` must match `/etc/rgos-device`), unknown files, bad paths, damaged tar, too little free space: nothing is changed and the old system boots.
* Replaced files are kept as `*.prev` on the boot partition when there is room. To roll back by hand, put the card in a PC and rename them back.
* An update that is itself broken (it boots but the launcher fails) is fixed the same way, or by flashing a newer tar after restoring the `.prev` files.
* Kernel modules: the initramfs now also carries `fs/exfat`, `fs/fat` and `fs/nls` modules (needed to read an exFAT card); override with `RGOS_MODULE_FILTER`.

Manual test on a PC: `rgos-update apply TAR BOOTDIR` (set `RGOS_DEVICE_FILE`, `RGOS_UPDATE_LOG`, `RGOS_UPDATE_TTY` to local paths).

## If an update fails

* The reason is the last line of `update/rgos-update.log` (next to the tar) and is also shown on the console. The tar is renamed `*.failed`; rename it back to `.tar` after fixing the cause to retry.
* `not enough free space`: the new files are staged beside the old ones, so free about the size of the new SYSTEM/initramfs on the FAT partition.
* `boot partition is not writable`: the FAT partition was mounted read-only (dirty flag); repair it on a PC.
