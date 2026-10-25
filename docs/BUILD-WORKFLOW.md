# Build RGOS in GitHub Actions

The workflow at `.github/workflows/build-rgos.yml` manually builds either a flashable disk image (`.img`) or an on-device update package (`.tar`) for one of two boards: **rg-rotate** (default; `boards/other/rg-rotate/build.sh`) or **rg34xxsp** (the Buildroot build in the repo root).

## Run it

1. Push this repository to GitHub with the workflow file included.
2. Open **Actions → Build RGOS image or update → Run workflow**.
3. Pick the **board**, then choose `img` or `update-tar`.
   - rg-rotate `img`: artifact `rg-rotate-rgos-<id>-img` containing `rg-rotate-rgos.img.gz` (gunzip it, then flash). The run checks that partition 1 is labelled `RGOS`, partition 3 is `uboot`, and `extlinux.conf` has `rdinit=/rgos-init` and no `LABEL=ROCKNIX`.
   - rg-rotate `update-tar`: `rgos-update-<id>.tar` (manifest device `rg-rotate`, SHA-256 checked). Tick *update_extlinux* to include `extlinux.conf`.
   - rg34xxsp `img` / `update-tar`: `rgos-rg34xxsp.img` / `rgos-update-<id>.tar` as before.
4. Optionally provide a build ID. Board options: *ddr_variant* (rg34xxsp), *build_psx* and *rocknix_url* (rg-rotate; turning PSX off skips the slow emulated source build).
5. When the run succeeds, open its summary and download the artifact under **Artifacts**.

For rg-rotate the workflow installs debootstrap, qemu-user-static and the partitioning tools, frees runner disk space, runs `sudo ./build.sh image|update` (Debian arm64 rootfs on the stock ROCKNIX-T618 image), and validates the result. For rg34xxsp it fetches ROCKNIX boot assets and builds Buildroot/RetroArch/mGBA. Update tar files are checked against the SHA-256 hashes in `manifest.json`. Artifacts are retained for 14 days.

## Runner requirements

The build is large and can take hours. GitHub-hosted runners have limited free disk space; Buildroot output plus the downloaded ROCKNIX image may exceed it. If a run fails with `No space left on device` or is killed for memory pressure, use a Linux self-hosted runner with substantial free disk space (suggested: 50+ GB free disk and 16+ GB RAM), or adapt the workflow to provision more storage. The workflow limits compilation to two parallel jobs to reduce memory pressure.

## Important

- An `.img` is a whole-disk image intended to be flashed to an SD card; flashing overwrites the selected drive.
- An update `.tar` is copied to the `update/` directory on the ROMS partition. It is not a disk image.
- The workflow runs only when manually started; it does not build on every push.

## Building locally

`./build.sh rg-rotate [image|update]` from the repository root runs the RG Rotate build (`boards/other/rg-rotate/build.sh`), using `sudo` when it is not already root. Output goes to `boards/other/rg-rotate/out/`.
