"""Second SD card: boot-disk detection, candidate disks, partition choice, mount/unmount sync, menu listing (fake sysfs + fake mount/blkid)."""
import json, os, stat, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "overlay", "opt", "rgos"))
D = tempfile.mkdtemp()
os.environ.update(RGOS_SDCARD_CONF=D + "/none.json", RGOS_SDCARD_STATUS=D + "/run/sdcard2.json", RGOS_SDCARD_LOCK=D + "/run/lock",
                  RGOS_SDCARD_LOG=D + "/log", RGOS_RUN=D + "/run", RGOS_NO_CARD2="")
import sdcard

SYS = D + "/sys"; DEV = D + "/dev"; MNT = D + "/media/roms2"
sdcard.SYS_BLOCK, sdcard.DEV_DIR, sdcard.PROC_MOUNTS = SYS, DEV, D + "/mounts"
sdcard.SYS_DEV_BLOCK = D + "/devblock"
CALLS, BLKID = [], {}


def fake_sh(args, timeout=15):
    CALLS.append(args)
    if args[0] == "blkid":
        info = BLKID.get(os.path.basename(args[-1]))
        return (0, "".join("%s=%s\n" % kv for kv in info.items())) if info else (2, "")
    if args[0] == "mount":
        dev, mp = args[-2], args[-1]
        with open(sdcard.PROC_MOUNTS, "a") as f:
            f.write("/dev/%s %s %s rw 0 0\n" % (os.path.basename(dev), mp, args[2]))
        return 0, ""
    if args[0] == "umount":
        mp = args[-1]
        lines = [l for l in open(sdcard.PROC_MOUNTS) if l.split()[1] != mp]
        open(sdcard.PROC_MOUNTS, "w").writelines(lines)
        return 0, ""
    return 0, ""
sdcard.sh = fake_sh


def disk(name, size=8 * 2 ** 21, parts=()):
    os.makedirs("%s/%s" % (SYS, name), exist_ok=True)
    open("%s/%s/size" % (SYS, name), "w").write(str(size))
    for n, sz in parts:
        os.makedirs("%s/%s/%s" % (SYS, name, n), exist_ok=True)
        open("%s/%s/%s/size" % (SYS, name, n), "w").write(str(sz))


def reset(mounts=("/dev/mmcblk0p2 / ext4 rw 0 0", "/dev/mmcblk0p1 /boot vfat rw 0 0", "/dev/mmcblk0p3 /media/roms exfat rw 0 0")):
    import shutil
    for d in (SYS, DEV, D + "/run"):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(D + "/run"); BLKID.clear(); del CALLS[:]
    open(sdcard.PROC_MOUNTS, "w").write("\n".join(mounts) + "\n")
    disk("mmcblk0", parts=[("mmcblk0p1", 100000), ("mmcblk0p2", 2000000), ("mmcblk0p3", 20000000)])


class T(unittest.TestCase):
    def setUp(self):
        reset()

    def test_one_card_nothing_happens(self):
        self.assertEqual(sdcard.candidate_disks(), [])
        self.assertIsNone(sdcard.sync())
        self.assertIsNone(sdcard.status())
        self.assertEqual(sdcard.extra_roots(), [])
        self.assertFalse([c for c in CALLS if c[0] == "mount"])

    def test_boot_disk_is_not_always_mmcblk0(self):
        reset(["/dev/mmcblk1p2 / ext4 rw 0 0", "/dev/mmcblk1p3 /media/roms exfat rw 0 0"])
        disk("mmcblk1"); self.assertEqual(sdcard.boot_disk(), "mmcblk1"); self.assertEqual(sdcard.candidate_disks(), ["mmcblk0"])

    def test_second_card_is_mounted_and_listed(self):
        disk("mmcblk1", parts=[("mmcblk1p1", 30000000)])
        BLKID["mmcblk1p1"] = {"TYPE": "exfat", "LABEL": "GAMES"}
        want = sdcard.sync()
        self.assertEqual(want["dev"], DEV + "/mmcblk1p1")
        self.assertEqual(sdcard.extra_roots(), [sdcard.DEFAULTS["mountpoint"]])
        self.assertTrue([c for c in CALLS if c[0] == "mount" and "exfat" in c])
        self.assertEqual(sdcard.sync()["dev"], DEV + "/mmcblk1p1")          # second run: already right, no second mount
        self.assertEqual(len([c for c in CALLS if c[0] == "mount"]), 1)

    def test_card_removed_is_unmounted(self):
        disk("mmcblk1", parts=[("mmcblk1p1", 30000000)]); BLKID["mmcblk1p1"] = {"TYPE": "vfat"}
        sdcard.sync(); self.assertTrue(sdcard.status())
        import shutil; shutil.rmtree(SYS + "/mmcblk1")
        self.assertIsNone(sdcard.sync()); self.assertIsNone(sdcard.status())
        self.assertTrue([c for c in CALLS if c[0] == "umount"])

    def test_picks_roms_label_then_biggest_and_skips_system_labels(self):
        disk("mmcblk1", parts=[("mmcblk1p1", 100000), ("mmcblk1p2", 4000000), ("mmcblk1p3", 90000000), ("mmcblk1p4", 5000)])
        BLKID.update({"mmcblk1p1": {"TYPE": "vfat", "LABEL": "RGBOOT"}, "mmcblk1p2": {"TYPE": "exfat", "LABEL": "ROMS"},
                      "mmcblk1p3": {"TYPE": "exfat", "LABEL": "BIG"}, "mmcblk1p4": {"TYPE": "vfat"}})
        conf = sdcard.load_conf()
        self.assertEqual(sdcard.pick_partition("mmcblk1", conf)["dev"], DEV + "/mmcblk1p2")   # ROMS label wins over size
        del BLKID["mmcblk1p2"]
        self.assertEqual(sdcard.pick_partition("mmcblk1", conf)["dev"], DEV + "/mmcblk1p3")   # RGBOOT and tiny ones ignored
        BLKID["mmcblk1p3"] = {"TYPE": "swap"}
        self.assertIsNone(sdcard.pick_partition("mmcblk1", conf))

    def test_card_without_partition_table(self):
        disk("mmcblk1"); BLKID["mmcblk1"] = {"TYPE": "vfat"}
        self.assertEqual(sdcard.pick_partition("mmcblk1", sdcard.load_conf())["dev"], DEV + "/mmcblk1")

    def test_wrong_roms_mount_is_healed(self):
        reset(["/dev/mmcblk0p2 / ext4 rw 0 0", "/dev/mmcblk1p1 /media/roms exfat rw 0 0"])
        disk("mmcblk1", parts=[("mmcblk1p1", 30000000)])
        BLKID["mmcblk0p3"] = {"TYPE": "exfat", "LABEL": "ROMS"}
        self.assertTrue(sdcard.heal_primary(sdcard.mounts(), sdcard.load_conf()))
        self.assertEqual(sdcard.disk_backing("/media/roms"), "mmcblk0")

    def test_disabled(self):
        disk("mmcblk1", parts=[("mmcblk1p1", 30000000)]); BLKID["mmcblk1p1"] = {"TYPE": "vfat"}
        c = D + "/off.json"; json.dump({"enabled": False}, open(c, "w")); sdcard.CONF_FILES[:] = [c]
        try: self.assertIsNone(sdcard.sync())
        finally: sdcard.CONF_FILES[:] = [D + "/none.json"]

    def test_cli(self):
        self.assertEqual(sdcard.cli(["status"]), 1); self.assertEqual(sdcard.cli(["bootdisk"]), 0); self.assertEqual(sdcard.cli(["nope"]), 2)


class MenuScan(unittest.TestCase):
    def test_scan_lists_both_cards(self):
        c1, c2 = D + "/c1", D + "/c2"
        for root, f in ((c1, "ports/Alpha.sh"), (c2, "ports/Beta.sh"), (c2, "gba/Gamma.gba"), (c2, "nes/Delta.nes")):
            os.makedirs(os.path.dirname("%s/roms/%s" % (root, f)), exist_ok=True); open("%s/roms/%s" % (root, f), "w").close()
        os.environ["RGOS_ROMS"] = c1
        sys.modules.pop("menu", None)
        import types
        from unittest import mock
        with mock.patch.dict(sys.modules, {"pygame": mock.MagicMock()}):
            import menu
        menu.sdcard = types.SimpleNamespace(extra_roots=lambda: [c2])
        g = menu.scan_roms()
        self.assertEqual([(x["title"], x["card"]) for x in g if x["sid"] == "ports"], [("Alpha", 1), ("Beta", 2)])
        self.assertEqual({x["sid"] for x in g}, {"ports", "gba", "nes"})   # include every supported system with a recognized ROM; NES is supported too
        menu.sdcard = None
        self.assertEqual([x["title"] for x in menu.scan_roms()], ["Alpha"])


if __name__ == "__main__":
    unittest.main()
