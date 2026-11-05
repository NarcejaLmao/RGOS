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

`helpers.py` loads the real `menu.py` and `rgos-input` for the tests.
