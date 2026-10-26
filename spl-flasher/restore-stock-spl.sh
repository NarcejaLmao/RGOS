#/usr/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! "$SCRIPT_DIR"/spd_dump-linux --wait 300 keep_charge 1 \
    fdl "$SCRIPT_DIR"/fdl1-dl.bin 0x5500 \
    fdl "$SCRIPT_DIR"/fdl2-dl.bin 0x9EFFFE00 \
    exec \
    write_part splloader "$SCRIPT_DIR"/spl_stock.img \
    write_part splloader_bak "$SCRIPT_DIR"/spl_stock.img \
    poweroff; then
    echo "SPL flash failed! If this looks like a USB permissions error, try re-running with sudo." >&2
    read -n 1 -s -r -p "Press any key to exit..."
    echo
    exit 1
fi

echo "SPL flash complete!"
echo "Your device has been restored to the stock SPL!"
read -n 1 -s -r -p "Press any key to exit..."
echo
