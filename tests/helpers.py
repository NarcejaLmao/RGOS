"""Shared loaders for the RG OS tests: they import the real menu.py and rgos-input without pygame, a device or root.

menu.py needs pygame and the device-side helper modules at import time, so those are replaced by mocks; every path the
code reads from the environment is pointed at a temporary directory first.
"""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RGOS = os.path.join(ROOT, "rgos")

_STUBS = ("pygame", "evpad", "fbout", "splash", "wifictl", "btctl", "sshctl", "filemanager", "sdcard", "hwctl")
_cache = {}


def sandbox():
    """A fresh temporary directory (deleted by the OS later); returns its path."""
    return tempfile.mkdtemp(prefix="rgos-test-")


def load_menu():
    """The real menu.py, imported once. Its RUN_DIR / BTN_MAP point at a temp dir; tests that need a clean one call
    fresh_wizard_env() to repoint them."""
    if "menu" in _cache:
        return _cache["menu"]
    tmp = sandbox()
    os.environ.setdefault("RGOS_RUN", os.path.join(tmp, "run"))
    os.environ.setdefault("RGOS_BTNMAP", os.path.join(tmp, "button-map.json"))
    os.environ.setdefault("RGOS_ROMS", os.path.join(tmp, "roms"))
    os.environ.setdefault("RGOS_STATE", os.path.join(tmp, "state.json"))
    os.environ.setdefault("RGOS_CALIBDONE", os.path.join(tmp, "calibrated"))
    saved = {n: sys.modules.get(n) for n in _STUBS}
    for n in _STUBS:
        sys.modules[n] = mock.MagicMock(name=n)
    sys.path.insert(0, RGOS)
    try:
        spec = importlib.util.spec_from_file_location("rgos_menu", os.path.join(RGOS, "menu.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(RGOS)
        for n, old in saved.items():             # leave no mocks behind for other imports
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    _cache["menu"] = mod
    return mod


def fresh_wizard_env(menu):
    """Point the wizard's run dir and button-map file at new empty temp paths; returns (run_dir, btn_map_path)."""
    tmp = sandbox()
    run = os.path.join(tmp, "run")
    os.makedirs(run)
    menu.RUN_DIR = run
    menu.BTN_MAP = os.path.join(tmp, "button-map.json")
    menu.set_swap_ab = lambda on: None           # the wizard resets A/B swap and restarts the hub when it saves: not in tests
    menu.restart_hub = lambda: None
    return run, menu.BTN_MAP


def load_hub():
    """rgos-input (a script with no .py extension) as a module."""
    if "hub" not in _cache:
        loader = importlib.machinery.SourceFileLoader("rgos_input", os.path.join(RGOS, "rgos-input"))
        spec = importlib.util.spec_from_loader("rgos_input", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        _cache["hub"] = mod
    return _cache["hub"]
