# Tests

Plain `unittest`, standard library only: no pygame, device or root needed (`menu.py` is imported with its hardware modules mocked).

    python3 -m unittest discover -s tests -t . -v      # run everything, from the repo root
    python3 -m unittest tests.test_buttonmap           # one file

| File | Covers |
| --- | --- |
| `test_buttonmap.py` | Map buttons wizard: button steps, L3/R3, stick calibration, skipping, retrying a failed step. Runs against a fake hub and a fake clock, so it is instant. |
| `test_hub.py` | `rgos-input`: stick scaling and deadzone, loading the calibration, the live stick feed the wizard reads, button translation. |
| `test_info.py` | Settings > Information text helpers and row list. |
| `test_stick_nav.py` | Menu navigation with the left stick: directions, threshold and hysteresis, auto-repeat, screen-off reset. |
| `test_filemanager.py` | File manager: protected boot files stay protected (including names that start with `..`). |
| `test_volume.py` | `rgos-volume`: the ALSA card from `hw.json` (card 0 is a real card, not "unset"). |
| `test_dhcp.py` | `rgos-wifi-dhcp`: lease script order (routes flushed before the address is added, so the default route sticks) and resolv.conf. |
| `test_btctl.py` | `btctl`: the saved Bluetooth on/off setting is applied at boot (off stays off). |
| `test_splash.py` | `splash.read_conf`: a malformed splash.json falls back to the defaults instead of raising. |

`helpers.py` loads the real `menu.py` and `rgos-input` for the tests.
