# Build RGOS in GitHub Actions

The workflow at `.github/workflows/build-rgos.yml` manually builds either a flashable disk image (`.img`) or an on-device update package (`.tar`).

## Run it

1. Push this repository to GitHub with the workflow file included.
2. Open **Actions → Build RGOS image or update → Run workflow**.
3. Choose `img` to build `rgos-rg34xxsp.img`, or `update-tar` to build `rgos-update-<build-id>.tar`.
4. Optionally provide a build ID and choose the ROCKNIX DDR variant, then start the run.
5. When the run succeeds, open its summary and download the artifact under **Artifacts**.

The workflow installs host dependencies, fetches ROCKNIX boot assets, builds Buildroot/RetroArch/mGBA, and validates the resulting artifact. Update tar files are checked against the SHA-256 hashes in `manifest.json`. Artifacts are retained for 14 days.

## Runner requirements

The build is large and can take hours. GitHub-hosted runners have limited free disk space; Buildroot output plus the downloaded ROCKNIX image may exceed it. If a run fails with `No space left on device` or is killed for memory pressure, use a Linux self-hosted runner with substantial free disk space (suggested: 50+ GB free disk and 16+ GB RAM), or adapt the workflow to provision more storage. The workflow limits compilation to two parallel jobs to reduce memory pressure.

## Important

- An `.img` is a whole-disk image intended to be flashed to an SD card; flashing overwrites the selected drive.
- An update `.tar` is copied to the `update/` directory on the ROMS partition. It is not a disk image.
- The workflow runs only when manually started; it does not build on every push.
