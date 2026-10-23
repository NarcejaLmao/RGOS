"""Tools > PortMaster launcher: installs the stable release, runs the app, and reports every failure on screen."""
import json, os, shutil, stat, subprocess, tempfile, unittest, zipfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay", "opt", "rgos", "PortMaster.sh")
if not os.path.exists(SCRIPT):
    SCRIPT = "/opt/rgos/PortMaster.sh"

CURL = """#!/bin/bash
# fake curl: serves $SHIM_RELEASES for the API URL and $SHIM_ZIP for downloads; SHIM_OFFLINE=1 = no network
[ -n "$SHIM_OFFLINE" ] && exit 6
out=""; url=""
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; -*) shift;; *) url=$1; shift;; esac; done
echo "$url" >> "$SHIM_LOG"
case "$url" in
  *api*) cp "$SHIM_RELEASES" "$out";;
  *.zip) cp "$SHIM_ZIP" "$out";;
  *) exit 22;;
esac
"""


def release(tag, channel, asset="PortMaster.zip"):
    return {"tag_name": tag, "name": tag, "body": "Release: %s (%s)" % (tag, channel), "draft": False,
            "assets": [{"name": asset, "browser_download_url": "https://dl.example/%s/%s" % (tag, asset)}]}


class PortMaster(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.roms = os.path.join(self.t, "media", "roms")
        os.makedirs(os.path.join(self.roms, "roms", "ports"))
        self.link = os.path.join(self.t, "roms-link")
        self.tty = os.path.join(self.t, "tty")
        self.shim = os.path.join(self.t, "shim"); os.makedirs(self.shim)
        with open(os.path.join(self.shim, "curl"), "w") as f:
            f.write(CURL)
        os.chmod(os.path.join(self.shim, "curl"), 0o755)
        self.curl_log = os.path.join(self.t, "curl.log")
        self.zip = os.path.join(self.t, "PortMaster.zip")
        self.set_app('echo "APP RAN cwd=$(pwd)"')

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def set_app(self, body):
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("PortMaster/PortMaster.sh", "#!/bin/bash\n%s\n" % body)
            z.writestr("PortMaster/version", "TESTVER\n")

    def write_releases(self, rels):
        self.releases = os.path.join(self.t, "releases.json")
        with open(self.releases, "w") as f:
            json.dump(rels, f)

    def run_script(self, offline=False):
        env = dict(os.environ, PATH=self.shim + os.pathsep + os.environ["PATH"], ROMS_ROOT=self.roms,
                   PORTS_DIR=os.path.join(self.roms, "roms", "ports"), RGOS_ROMS_LINK=self.link, RGOS_TTY=self.tty,
                   RGOS_FAIL_PAUSE="0", SHIM_LOG=self.curl_log, SHIM_ZIP=self.zip,
                   SHIM_RELEASES=getattr(self, "releases", ""), SHIM_OFFLINE="1" if offline else "")
        p = subprocess.run(["bash", SCRIPT], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=60)
        return p.returncode, p.stdout

    def screen(self):
        if not os.path.exists(self.tty):
            return ""
        with open(self.tty) as f:
            return f.read()

    def downloads(self):
        if not os.path.exists(self.curl_log):
            return []
        with open(self.curl_log) as f:
            return f.read().split()

    def test_fresh_install_picks_the_stable_release_not_the_newest_alpha(self):
        self.write_releases([release("2026.09.13-0343", "alpha"), release("2026.09.08-0809", "beta"),
                             release("2026.06.23-0015", "stable")])
        rc, out = self.run_script()
        self.assertEqual(rc, 0, out)
        self.assertIn("https://dl.example/2026.06.23-0015/PortMaster.zip", self.downloads())
        self.assertNotIn("https://dl.example/2026.09.13-0343/PortMaster.zip", self.downloads())
        self.assertIn("APP RAN", out)
        self.assertTrue(os.path.isfile(os.path.join(self.roms, "roms", "ports", "PortMaster", "PortMaster.sh")))
        self.assertEqual(os.path.realpath(self.link), os.path.realpath(os.path.join(self.roms, "roms")))

    def test_app_is_told_to_use_alsa(self):
        self.write_releases([release("2026.06.23-0015", "stable")])
        self.set_app('echo "AUDIO=$SDL_AUDIODRIVER"')
        rc, out = self.run_script()
        self.assertEqual(rc, 0, out)
        self.assertIn("AUDIO=alsa", out)

    def test_falls_back_to_newest_when_no_stable_exists(self):
        self.write_releases([release("2026.09.13-0343", "alpha")])
        rc, out = self.run_script()
        self.assertEqual(rc, 0, out)
        self.assertIn("APP RAN", out)

    def test_installed_app_starts_without_network(self):
        self.write_releases([release("2026.06.23-0015", "stable")])
        self.assertEqual(self.run_script()[0], 0)
        rc, out = self.run_script(offline=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("APP RAN", out)

    def test_no_network_is_reported_on_screen(self):
        rc, out = self.run_script(offline=True)
        self.assertEqual(rc, 1)
        self.assertIn("connect Wi-Fi", self.screen())

    def test_rate_limited_api_is_reported_on_screen(self):
        self.write_releases({"message": "API rate limit exceeded"})
        rc, out = self.run_script()
        self.assertEqual(rc, 1)
        self.assertIn("rate limit", out)
        self.assertIn("did not contain PortMaster.zip", self.screen())

    def test_release_without_the_zip_asset_is_reported(self):
        self.write_releases([release("2026.06.23-0015", "stable", asset="Install.PortMaster.sh")])
        rc, _ = self.run_script()
        self.assertEqual(rc, 1)
        self.assertIn("did not contain PortMaster.zip", self.screen())

    def test_app_that_dies_at_once_is_reported_on_screen(self):
        self.write_releases([release("2026.06.23-0015", "stable")])
        self.set_app("echo boom; exit 3")
        rc, out = self.run_script()
        self.assertEqual(rc, 1)
        self.assertIn("exited with code 3", self.screen())

    def test_dangling_roms_link_is_replaced(self):
        self.write_releases([release("2026.06.23-0015", "stable")])
        os.symlink(os.path.join(self.t, "gone"), self.link)
        rc, out = self.run_script()
        self.assertEqual(rc, 0, out)
        self.assertEqual(os.path.realpath(self.link), os.path.realpath(os.path.join(self.roms, "roms")))

    def test_roms_path_pointing_elsewhere_is_reported(self):
        self.write_releases([release("2026.06.23-0015", "stable")])
        os.makedirs(self.link)
        rc, _ = self.run_script()
        self.assertEqual(rc, 1)
        self.assertIn("is not the ROMS card", self.screen())

    def test_unexpected_command_failure_is_not_silent(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        self.write_releases([release("2026.06.23-0015", "stable")])
        self.assertEqual(self.run_script()[0], 0)
        ports = os.path.join(self.roms, "roms", "ports")
        shutil.rmtree(os.path.join(ports, "PortMaster", "autoinstall"))
        os.chmod(os.path.join(ports, "PortMaster"), stat.S_IRUSR | stat.S_IXUSR)    # re-creating autoinstall now fails
        rc, _ = self.run_script()
        self.assertEqual(rc, 1)
        self.assertIn("unexpected error", self.screen())
        os.chmod(os.path.join(ports, "PortMaster"), 0o755)


if __name__ == "__main__":
    unittest.main()
