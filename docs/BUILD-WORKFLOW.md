# Build RGOS in GitHub Actions

The workflow at `.github/workflows/build-rgos.yml` manually builds either a flashable disk image (`.img`) or an on-device update package (`.tar`) for the **Anbernic RG34XXSP** and the **Anbernic RG Rotate** (`build.sh`: a Debian arm64 rootfs on each device's stock ROCKNIX image).

## Run it

1. Push this repository to GitHub with the workflow file included.
2. Open **Actions → Build RGOS image or update → Run workflow**.
3. Choose the `device` (`rg34xxsp`, `rg-rotate` or `all`) and `img` or `update-tar`.
   - `img`: artifact `rgos-<device>-<id>-img` containing `<device>-rgos.img.gz` (gunzip it, then flash). The run checks that partition 1 is labelled `RGOS`, `extlinux.conf` has `rdinit=/rgos-init` and no `LABEL=ROCKNIX`, and then the device-specific part: RG Rotate = partition 3 is `uboot` (GPT); RG34XXSP = MBR image with `dtb.img` present.
   - `update-tar`: `rgos-update-<device>-<id>.tar` (manifest device `rg-rotate` or `rg34xxsp`, SHA-256 checked). Tick *update_extlinux* to include `extlinux.conf`.
4. Optionally provide a build ID, turn off *build_psx* (skips the slow emulated PlayStation core build) or override *rocknix_url* (not with `device=all`: each device has its own stock image).
5. When the run succeeds, open its summary and download the artifact under **Artifacts**.

The workflow installs debootstrap, qemu-user-static and the partitioning tools, frees runner disk space, runs `./build.sh <device> image|update`, and validates the result. Update tar files are checked against the SHA-256 hashes in `manifest.json`. Artifacts are retained for 14 days.

## Runner requirements

The build is large and can take hours. GitHub-hosted runners have limited free disk space; the Debian rootfs plus the downloaded ROCKNIX image may exceed it. If a run fails with `No space left on device` or is killed for memory pressure, use a Linux self-hosted runner with substantial free disk space (suggested: 50+ GB free disk and 16+ GB RAM), or adapt the workflow to provision more storage.

## Important

- An `.img` is a whole-disk image intended to be flashed to an SD card; flashing overwrites the selected drive.
- An update `.tar` is copied to the `update/` directory on the ROMS partition. It is not a disk image.
- The workflow runs only when manually started; it does not build on every push.

## Building locally

`./build.sh <rg34xxsp|rg-rotate|all> [image|update]` from the repository root, using `sudo` when it is not already root. **A device is required**: `sudo ./build.sh` on its own only prints the usage text and does nothing. Output goes to `out/` (`rg34xxsp-rgos.img`, `rg-rotate-rgos.img`); each device has its own work directory under `work/<device>/`. `all` builds the devices one after the other and needs roughly twice the disk space.

RG34XXSP specifics (see the header of `build.sh`): the base is the stock ROCKNIX H700 image (`ROCKNIX-H700.aarch64-<release>-DDR4.img.gz` from the ROCKNIX releases; `H700_RAM=DDR3` / `ROCKNIX_H700_VERSION=<tag>` / `ROCKNIX_URL=<url>` change it). The image has an MBR, so there is no `uboot` partition and no SPL to flash; the build copies `device_trees/sun50i-h700-anbernic-rg34xx-sp.dtb` to `dtb.img` (`RG34XXSP_DTB=sun50i-h700-anbernic-rg34xx-sp-v2-panel` for the v2 panel) and then removes `device_trees/`, so `dtb.img` is the only device tree on the RGOS partition. The ROMS partition is created on first boot as partition 3.
