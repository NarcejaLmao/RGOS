# Troubleshooting: the card does not boot

RG OS leaves up to four evidence files on the FAT boot partition (the one labelled ROCKNIX). Put the card in a PC and look at which exist.

| File on the FAT partition | Written by | If it is missing |
|---|---|---|
| `rgos-initramfs.txt` | the RG OS initramfs (`initramfs/init`) | the initramfs never ran (see below) |
| `rgos-bootinfo.txt` | `rgos-init`, 6 s after switch_root | the failure is between switch_root and the session |
| `rgos-logs.txt` | `rgos-session`, at 8, 28 and 58 s | the session never started, or it died early |
| `rgos-retroarch.txt` | `rgos-launch`, after every game | no game was launched yet |

## Nothing is written at all (RG Rotate: boots Android, or stays black)

1. Android again: the SPL did not find U-Boot. Partition 3 of the card must be the GPT partition named `uboot` (`sgdisk -p /dev/sdX`); re-flash the image and the SPL.
2. Black screen, no `rgos-initramfs.txt`: U-Boot started but the RG OS initramfs did not run. Copy `extlinux/extlinux.conf.stock` over `extlinux/extlinux.conf` on the FAT partition and boot again.
   * Boots (slowly): the custom initramfs is the problem on this U-Boot/kernel (for example U-Boot has no `ramdisk_addr_r`, or the kernel was built to ignore an external initrd). Send `extlinux.conf` and a serial log if you have one.
   * Still does not boot: the problem is before the initramfs (SPL, U-Boot, kernel, device tree).

## `rgos-initramfs.txt` exists

Read it from the top: it lists the kernel command line, the partition the boot files were found on, `/proc/partitions` and the last step reached.

* `cannot mount /flash/SYSTEM as squashfs`: the file listing, `/proc/filesystems`, loop devices and the kernel log tail are included. A missing `squashfs` or `loop` entry means the kernel cannot mount SYSTEM; a damaged `SYSTEM` means re-flash the card.
* `switch_root to /usr/sbin/rgos-init` is the last line and there is no `rgos-bootinfo.txt`: the new root started but PID 1 died. Check `rgos-logs.txt` if it exists, otherwise rebuild with a fresh image.
