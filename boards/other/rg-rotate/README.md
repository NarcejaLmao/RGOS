# rg-rotate-rgos

A bare-bones Debian (arm64) SD card image for the Anbernic RG Rotate that boots into the RG OS menu/launcher,
with RetroArch (aarch64) and the Gambatte (Game Boy / Game Boy Color), mGBA (Game Boy Advance), Snes9x (SNES), Beetle WonderSwan, Beetle NGP (Neo Geo Pocket) and PCSX-ReARMed (PlayStation, Beetle PSX as fallback) cores.

## How it works
- `build.sh` downloads the ROCKNIX-T618 image and copies it (partition table, bootloader area, kernel, DTB unchanged). Its `SYSTEM`
  squashfs is replaced with a minimal Debian rootfs (python3-pygame, busybox, kmod, ...), and `extlinux.conf` is patched to use the
  RG OS initramfs (below).
- **Initramfs:** ROCKNIX's own init (inside its kernel) prints "NEVER TOUCH boot=..." and waits 60 s whenever it is unhappy with the
  card. `initramfs/` replaces it: `build.sh` adds `rgos-initrd.cpio` to the FAT partition and puts `INITRD /rgos-initrd.cpio` plus
  `rdinit=/rgos-init` in `extlinux.conf`, so the kernel runs `initramfs/init` and ROCKNIX's init never starts. It finds the FAT
  partition that holds `SYSTEM`, loop-mounts it, moves `/flash` into it and `switch_root`s to `rgos-init`. It prints nothing unless
  the card can't be read, never counts down, and keeps retrying instead of giving up. It no longer repartitions p2 either.
- **Partition label:** `build.sh` relabels the FAT boot partition from `ROCKNIX` to `RGOS` (`fatlabel`), changes `boot=LABEL=ROCKNIX` to `LABEL=RGOS` in `extlinux.conf` (and so in the `.stock` recovery copy), and sets the GPT name of partition 1 to `RGOS`. Cards flashed earlier keep the old label; the initramfs finds the partition by its `SYSTEM` file, so both boot.
- **Going back to ROCKNIX's init:** from a PC, copy `extlinux/extlinux.conf.stock` over `extlinux/extlinux.conf` on the FAT partition.
- RG OS (`rgos/`: menu.py, rgos-session, rgos-input, ...) is installed to `/opt/rgos`. `rgos-init` (PID 1) loads display
  modules if needed and runs `rgos-session`; if the session ever exits it shows a white screen instead of panicking.
- `spl-flasher/` is needed once to put the SPL on the device so it can boot from SD.
- Logs are copied to `rgos-logs.txt` on the card's FAT partition 8, 28 and 58 s after boot.

## Use
    sudo apt install debootstrap qemu-user-static binfmt-support squashfs-tools wget
    sudo bash build.sh
    cd spl-flasher && ./flash-spl.sh          # device powered off, hold Home/Back while plugging in USB
    sudo dd if=out/rg-rotate-rgos.img of=/dev/sdX bs=4M conv=fsync status=progress

Restore stock SPL with `spl-flasher/restore-stock-spl.sh` (see spl-flasher/README.txt).
`inspect-kernel.sh` dumps the ROCKNIX kernel's embedded initramfs for debugging. The kernel log line `rgos-initramfs: boot partition found after N tries` (dmesg, or `rgos-logs.txt`) shows the new init ran.

## ROMS partition (partition 4, exFAT)
- The image ships partitions 1 and 2 only. On the **first boot** `rgos/rgos-roms` (called by `rgos-session`) moves the backup GPT to the
  real end of the card, creates partition 4 (label `ROMS`, fixed GUID, type 0700) from the first 1 MiB boundary after partition 2 to the
  last usable sector, so it uses **all the free space**, and formats it exFAT (label `ROMS`). The first boot takes a few
  seconds longer. Later boots find partition 4 and do nothing. `build.sh` no longer needs `ROMS_SIZE_MB` or `exfatprogs` on the PC.
- **Partition 3 (`uboot`) must never be deleted or renamed**: the SPL finds U-Boot on the card by that GPT name. Without it the device ignores the card and
  boots Android from its internal storage. Cards flashed from the previous build lack it: re-flash with this image.
- Safety: before touching anything the script saves the GPT to `rgos-gpt-backup.bin` on the FAT partition (restore from a PC with
  `sgdisk -l rgos-gpt-backup.bin <disk>`), edits the table in a single `sgdisk` call, never touches partitions 1, 2 and 3, never formats
  a partition 4 that is not named ROMS or already has a filesystem, and resumes after a power cut. Its log is `rgos-roms.log` inside `rgos-logs.txt`.
  An earlier build that did this on first boot left cards that would not boot; if yours does not boot after the first start, restore that backup.
- Everything user-facing is on the ROMS partition: ROMs, saves, states, BIOS, screenshots, favorites, music, update tars, **and the settings**
  (`rgos-data/`: button map, Wi-Fi, SSH keys, Bluetooth pairings, menu state). On the first boot with a ROMS partition the old
  `rgos-data` on the boot partition is copied over; that old copy is left there as a backup and only used if ROMS cannot be mounted.
  Only boot files, `rgos-bootinfo.txt`, `rgos-logs.txt`, `rgos-retroarch.txt` and the optional `ssh-password.txt` / `authorized_keys` / `retroarch-joypad.txt` stay on the FAT partition.
- Windows and Linux read exFAT directly, so ROMs can be copied from a PC. Put ROMs in `roms/gb/` and `roms/gbc/`.
- If partition 4 cannot be mounted, RG OS falls back to `/boot/roms` on the FAT boot partition.

## Wi-Fi, Bluetooth, SSH / file transfer
- **Wi-Fi** (Settings > Wi-Fi): `rgos-session` now starts the Wi-Fi daemon (`rgos/wifictl.py daemon`, with `rgos/rgos-wifi-dhcp` for the
  lease). Log: `rgos-wifi.log` section of `rgos-logs.txt`.
- **Bluetooth** (Settings > Bluetooth): BlueZ + D-Bus. At boot `rgos/btctl.py start` loads the Bluetooth kernel modules, unblocks rfkill,
  starts dbus and `bluetoothd`. In the menu: A pairs (Just Works), connects or disconnects, X scans, Y forgets. Pairings are kept in
  `rgos-data/bluetooth.tar.gz` on the boot partition. No audio (A2DP) stack is included: it is for controllers, keyboards and similar.
- **SSH / SFTP** (Settings > SSH / file transfer, on by default): Dropbear on port 22, user `root`, password `rgos`. Use
  `ssh root@<ip>`, or WinSCP / FileZilla / `scp` with SFTP to copy files (`/media/roms` is the ROMS partition, `/boot` the boot partition).
  Put `ssh-password.txt` (one line) on the boot partition to change the password, or `authorized_keys` to log in with a key.
  Host keys are generated on the first start and kept in `rgos-data/ssh/`. The password is a known default: turn SSH off in the menu on networks you do not trust.
- Not tested on hardware: whether the Wi-Fi/Bluetooth chip's kernel drivers and firmware (taken from the stock ROCKNIX image) bring up
  `wlan0` / `hci0`. If one does not show up, `rgos-logs.txt` on the boot partition says why (`no Bluetooth adapter ...`, `no-adapter`).

## Volume buttons and speaker
- `rgos/rgos-volume` runs from boot: it finds the speaker's ALSA card, un-mutes the output switches, restores the saved volume
  (`rgos-data/volume.json`, default 70 %) and handles **VOL+ / VOL- (5 % steps, hold to repeat)** and a mute key. The keys are read from every
  input device that has them, including the input hub, so it works wherever the hardware puts them. In the launcher a volume bar pops up
  (from `/run/rgos/volume`); in a game RetroArch shows "Volume: N%". From SSH: `python3 /opt/rgos/rgos-volume up|down|mute|set 60`.
- The launcher's own sounds (splash, music) now use the same ALSA card as the games (`AUDIODEV` is set by `rgos-session`).
- If there is no sound or the keys do nothing, `rgos-volume.log` in `rgos-logs.txt` lists the mixer controls found and which were used. Override
  with `rgos-data/hw.json`: `{"audio": {"card": 0, "controls": ["Speaker"], "step": 5}}`.

## Windows drive letters
- Partition order on the card is fixed: p1 RGOS, p2 STORAGE (ext4, Windows ignores it), p3 uboot (2 MiB, Windows ignores it), p4 ROMS. Windows decides the letters, and
  it remembers them per partition. RGOS is identical in every flash, so it keeps its old letter; ROMS used to get a new random
  GUID every time and so took the first free letter. ROMS now has a fixed partition GUID (`ROMS_GUID`
  in `rgos/rgos-roms`), which is what Windows keys the letter on, so a letter you set once in Disk Management (right-click the volume > Change Drive Letter) sticks across re-flashes.

## File manager drives
- The Storage list shows, in order: **RGOS** (the FAT boot partition, partition 1), the optional second SD card, and **ROMS** last
  (partition 4, the exFAT partition created on first boot).
- Boot files on RGOS (`SYSTEM`, `KERNEL`, `extlinux`, `rgos-data`, ...; see `BOOT_PROTECTED` in `rgos/menu.py`) cannot be deleted,
  renamed or moved from the file manager. Everything else there (e.g. `rgos-logs.txt`) can.

## Emulation
- RetroArch and the `gambatte` and `mgba` cores come from Debian packages (see `EMU_PKGS` in `build.sh`); `config/retroarch.cfg` and
  `config/autoconfig/RG_OS_Gamepad.cfg` are installed to `/etc/rgos/`. Snes9x (Debian non-free: debootstrap runs with `--components=main,non-free`) and `libretro-beetle-wswan` are in `EMU_PKGS`. The Neo Geo Pocket core (`mednafen_ngp`) has no Debian package: `build.sh` compiles it inside the rootfs from `NGP_SRC` (default: libretro/beetle-ngp-libretro master; `NGP_SRC=none` skips it), or copies any prebuilt aarch64 `*_libretro.so` found in `cores/`. `pcsx_rearmed` is built the same way from `PSX_REPO` (needs `git` on the build PC; `PSX_REPO=none` skips it); Debian's `libretro-beetle-psx` is installed as a best-effort fallback (skipped with a notice if the suite doesn't have it). If a core build fails the image is still produced without it. Add more cores with `EXTRA_PKGS="libretro-nestopia ..."`.
- Put ROMs in `roms/gba/` (`.gba`, mGBA; BIOS optional: `gba_bios.bin` in `bios/`), `roms/gb/` (`.gb`), `roms/gbc/` (`.gbc`), `roms/snes/` (`.sfc`, `.smc`), `roms/ws/` (WonderSwan) and `roms/wsc/` (WonderSwan Color), `roms/ngp/` (Neo Geo Pocket) and `roms/ngpc/` (Neo Geo Pocket Color) and `roms/psx/` (PlayStation: `.cue`+`.bin`, `.chd`, `.pbp`, `.m3u`; put a BIOS such as `scph1001.bin` in `bios/`) on the ROMS partition; saves and states go to `saves/<system>/`
  and `states/<system>/` next to them.
- In a game: MENU+START returns to the launcher, MENU+SELECT opens the RetroArch menu.

### RetroArch controls
The input hub merges every button into one virtual pad ("RG OS Gamepad"); `config/autoconfig/RG_OS_Gamepad.cfg` maps it (matched by name and
vendor/product id). RetroPad: B/A = bottom/right face, X/Y = top/left face, L/R, L2/R2, L3/R3, SELECT, START, D-pad. There are no analog sticks.
Hold **MENU** and press:

| Button | Action | Button | Action |
|---|---|---|---|
| START | back to the launcher | R2 (hold) | fast-forward |
| SELECT | RetroArch menu | A | pause / resume |
| R | save state | X | screenshot |
| L | load state | D-pad right / left | next / previous state slot |

The binds are written straight into `config/retroarch.cfg` (`input_player1_*_btn`); the autoconfig profile is no longer needed. Rewind is off (`rewind_enable`).

**Why buttons did nothing in games before:** the raw gamepad (`/dev/input/js0`) is grabbed by the input hub and silent, the hub's virtual pad is
`js1`, and there is no udev daemon to hide the raw one, so RetroArch used the dead `js0` as player 1. Now `rgos-launch` finds the hub's `jsN`
(by name) and passes `input_player1_joypad_index` on every launch, with the `linuxraw` joypad driver (reads `/dev/input/jsN`, no udev/SDL).
- After every game, `rgos-retroarch.txt` is written to the boot partition: the pads found, which one was used, the joypad driver, the run config
  and the RetroArch log. Send it if a button still does nothing.
- To try another joypad driver without rebuilding, put one word (`sdl2`, `udev` or `linuxraw`) in `retroarch-joypad.txt` on the boot partition.

## Offline updates (.tar)
- `sudo ./build.sh update` builds `out/rgos-update-<id>.tar` (the new `SYSTEM`, its `.md5` and `rgos-initrd.cpio`; set `UPDATE_ID=` to choose the id,
  `UPDATE_EXTLINUX=1` to include `extlinux.conf`). It does not touch `out/rg-rotate-rgos.img`.
- Copy the tar into the `update/` folder of the ROMS partition (or the boot partition) and reboot. `rgos-session` runs `rgos-update boot` before the
  menu: it checks the manifest, device name and SHA-256 of every file, stages them as `<name>.new`, renames the old ones to `<name>.prev`, swaps the new
  ones in and restarts. Any error renames everything back and the boot partition keeps working.
- The tar is renamed `.applied` or `.failed` and a result line is added to `update/rgos-update.log` on the card. An update whose id is already
  installed (`rgos-update.id` on the boot partition) is skipped. To roll back from a PC, rename `SYSTEM.prev` over `SYSTEM`.
- A full `SYSTEM` is about 500 MB; the boot partition needs roughly that much free space (an older `.prev` is deleted automatically if needed).
- `rgos/rgos-update apply TAR BOOTDIR` applies a tar to a mounted boot partition by hand.

## Display
The launcher draws a square 720x720 (1:1) canvas for the RG Rotate's square screen and scales it uniformly to the
framebuffer: no stretching, no black bars on a square panel. Set `W, H` in `rgos/menu.py` to change the canvas.

## Caveats (untested on hardware)
- The new initramfs is untested on hardware. It assumes the ROCKNIX kernel has FAT, squashfs, loop and the SD controller built in (its own init needs the same). The ROCKNIX boot splash no longer runs.
- Input code was written for other devices; there is no analog-stick/joystick support (buttons and D-pad only); the panel may need `RGOS_FB_ROTATE` in `rgos-session`.
- Override with env vars: `DEBIAN_SUITE`, `ROCKNIX_URL`.
