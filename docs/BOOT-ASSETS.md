# Automatic H700 boot-asset retrieval

`./build.sh build` now invokes `scripts/fetch-rocknix-assets.sh`. It queries the latest release at https://github.com/ROCKNIX/distribution/releases, selects the H700 image for the requested DDR variant, downloads and decompresses it, inspects its partition table, and extracts the FAT boot partition using `mtools` without requiring root mounting.

The script stages the RG34XXSP device tree `sun50i-h700-anbernic-rg34xx-sp.dtb` and upstream boot files under `assets/rocknix/`. Set `RGOS_H700_DDR=DDR3` to choose DDR3 rather than the default DDR4 asset. The image download is large and requires several GB of free space.

## Why a flashable image is not emitted yet

Boot files are coupled to the upstream bootloader, kernel, initramfs, kernel command line, and root filesystem expectations. The automatic asset retrieval is implemented, but the ROCKNIX initramfs still needs a verified change to mount and switch to the Buildroot root filesystem. Until that is done, the project intentionally does not write a misleading `rgos-rg34xxsp.img`.

Upstream references:
- https://github.com/ROCKNIX/distribution/releases
- https://rocknix.org/docs/configure/h700-installation/
- https://rocknix.org/devices/anbernic/rg34xx-sp/
