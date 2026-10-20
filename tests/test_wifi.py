"""Wi-Fi glue: PSK derivation, config generation, scan parsing, saved networks, rgos-wifi CLI with a fake wpa_cli."""
import os, stat, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "overlay", "opt", "rgos"))
D = tempfile.mkdtemp()
os.environ.update(RGOS_WIFI_CONF=D + "/wifi.json", RGOS_WIFI_SYS=D + "/none.json", RGOS_RUN=D + "/run",
                  RGOS_SYSNET=D + "/net", RGOS_WPA_CTRL=D + "/ctrl", RGOS_WPA_CLI=D + "/wpa_cli")
import wifictl

SCAN = ("bssid / frequency / signal level / flags / ssid\n"
        "aa:bb:cc:00:00:01\t2412\t-60\t[WPA2-PSK-CCMP][ESS]\tHome\n"
        "aa:bb:cc:00:00:02\t5180\t-48\t[WPA2-PSK-CCMP][ESS]\tHome\n"
        "aa:bb:cc:00:00:03\t2437\t-70\t[ESS]\tCafe\n"
        "aa:bb:cc:00:00:04\t2437\t-52\t[WPA2-PSK-CCMP][ESS]\t\n"
        "aa:bb:cc:00:00:05\t2437\t-80\t[WPA3-SAE-CCMP][ESS]\tNew3\n"
        "aa:bb:cc:00:00:06\t2437\t-66\t[WEP][ESS]\tOld\n"
        "aa:bb:cc:00:00:07\t2437\t-71\t[WPA2-PSK-CCMP][ESS]\tCaf\\xc3\\xa9 2\n")

class T(unittest.TestCase):
    def setUp(self):
        for f in (wifictl.CONF_FILE,):
            if os.path.exists(f): os.remove(f)
    def test_psk_matches_ieee_vector(self):
        self.assertEqual(wifictl.derive_psk("IEEE", "password"), "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e")
    def test_password_length(self):
        self.assertFalse(wifictl.password_ok("short")); self.assertTrue(wifictl.password_ok("longenough")); self.assertFalse(wifictl.password_ok("x" * 64))
    def test_parse_scan(self):
        n = {x["ssid"]: x for x in wifictl.parse_scan(SCAN)}
        self.assertEqual(n["Home"]["rssi"], -48)                       # strongest BSSID wins, one row per SSID
        self.assertEqual(set(n), {"Home", "Cafe", "New3", "Old", "Café 2"})   # hidden (empty) SSID dropped, \x escapes decoded
        self.assertTrue(n["Home"]["secured"] and not n["Cafe"]["secured"])
        self.assertTrue(n["New3"]["unsupported"] and n["Old"]["unsupported"] and not n["Home"]["unsupported"])
        self.assertEqual([x["ssid"] for x in wifictl.parse_scan(SCAN)][0], "Home")
    def test_bars(self):
        self.assertEqual([wifictl.bars(r) for r in (-40, -60, -70, -90, None)], [4, 3, 2, 1, 0])
    def test_save_and_conf(self):
        self.assertTrue(wifictl.add_network("Home", "password1"))
        self.assertTrue(wifictl.add_network("Cafe", None))
        self.assertTrue(wifictl.add_network("Hid", "password2", hidden=True))
        c = wifictl.load_conf()
        self.assertEqual([n["ssid"] for n in c["networks"]], ["Home", "Cafe", "Hid"])
        self.assertEqual(oct(os.stat(wifictl.CONF_FILE).st_mode & 0o777), "0o600")
        self.assertNotIn("password1", open(wifictl.CONF_FILE).read())   # only the derived key is stored
        txt = wifictl.make_wpa_conf(c, "ca")
        self.assertIn("country=CA", txt); self.assertIn("ssid=" + b"Home".hex(), txt)
        self.assertIn("psk=" + wifictl.derive_psk("Home", "password1"), txt)
        self.assertIn("key_mgmt=NONE", txt); self.assertIn("scan_ssid=1", txt)
        self.assertLess(txt.index(b"Home".hex()), txt.index(b"Hid".hex()))   # newest network last = highest priority
    def test_resave_replaces_and_prefers(self):
        wifictl.add_network("A", "password1"); wifictl.add_network("B", "password2"); wifictl.add_network("A", "password3")
        c = wifictl.load_conf()
        self.assertEqual([n["ssid"] for n in c["networks"]], ["B", "A"])
        wifictl.prefer("B")
        prio = {n["ssid"]: n["prio"] for n in wifictl.load_conf()["networks"]}
        self.assertGreater(prio["B"], prio["A"])
    def test_forget_and_toggle(self):
        wifictl.add_network("A", "password1"); wifictl.forget("A"); wifictl.set_enabled(False)
        c = wifictl.load_conf(); self.assertEqual(c["networks"], []); self.assertFalse(c["enabled"])
    def test_find_iface(self):
        os.makedirs(D + "/net/eth0", exist_ok=True); os.makedirs(D + "/net/wlan0/wireless", exist_ok=True)
        self.assertEqual(wifictl.find_iface(), "wlan0")
    def test_status_stale_and_text(self):
        self.assertEqual(wifictl.status()["state"], "off")
        wifictl.write_status({"state": "connected", "ssid": "Home", "ip": "10.0.0.5", "rssi": -50, "iface": "wlan0"})
        st = wifictl.status()
        self.assertEqual(wifictl.status_text(st), "Connected to Home   10.0.0.5")
        os.utime(wifictl.STATUS_FILE, (1, 1))
        self.assertEqual(wifictl.status()["state"], "off")
    def test_cli_connect_with_fake_wpa_cli(self):
        os.makedirs(D + "/ctrl", exist_ok=True); open(D + "/ctrl/wlan0", "w").close()
        os.makedirs(D + "/net/wlan0/wireless", exist_ok=True)
        with open(D + "/wpa_cli", "w") as f:
            f.write("#!/bin/sh\necho \"$@\" >> %s/wpa_cli.log\n" % D)
        os.chmod(D + "/wpa_cli", 0o755)
        exe = os.path.join(os.path.dirname(__file__), "..", "overlay", "opt", "rgos", "rgos-wifi")
        r = subprocess.run([sys.executable, exe, "connect", "Home", "password1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = open(D + "/wpa_cli.log").read()
        self.assertIn("reconfigure", log); self.assertIn("reassociate", log)
        self.assertIn("network={", open(wifictl.WPA_CONF).read())
        r = subprocess.run([sys.executable, exe, "connect", "Home", "short"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)

    def test_dhcp_script_syntax_and_noop(self):
        s = os.path.join(os.path.dirname(__file__), "..", "overlay", "opt", "rgos", "rgos-wifi-dhcp")
        self.assertEqual(subprocess.run(["sh", "-n", s]).returncode, 0)
        self.assertEqual(subprocess.run(["sh", s, "bound"], env={"PATH": "/usr/bin:/bin"}).returncode, 0)   # no $interface: does nothing

if __name__ == "__main__":
    unittest.main()
