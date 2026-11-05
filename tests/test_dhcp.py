"""rgos-wifi-dhcp: the udhcpc lease script must leave the interface with its subnet route and a default route."""
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import helpers

SCRIPT = os.path.join(helpers.RGOS, "rgos-wifi-dhcp")


class BoundTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rgos-dhcp-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.log = os.path.join(self.dir, "ip.log")
        fake = os.path.join(self.dir, "ip")
        with open(fake, "w") as f:                       # records every `ip` call instead of touching the network
            f.write('#!/bin/sh\necho "$*" >> "%s"\n' % self.log)
        os.chmod(fake, 0o755)

    def run_script(self, action, **env):
        e = dict(os.environ, PATH=self.dir + os.pathsep + os.environ["PATH"], RGOS_RUN=os.path.join(self.dir, "run"),
                 interface="wlan0")
        e.update(env)
        subprocess.run(["sh", SCRIPT, action], env=e, check=True)
        with open(self.log) as f:
            return [l.strip() for l in f]

    def test_routes_are_flushed_before_the_address_is_added(self):
        calls = self.run_script("bound", ip="192.168.1.50", mask="24", router="192.168.1.1")
        add = next(i for i, c in enumerate(calls) if c.startswith("addr add 192.168.1.50/24"))
        flush = next(i for i, c in enumerate(calls) if c.startswith("route flush"))
        default = next(i for i, c in enumerate(calls) if c.startswith("route add default via 192.168.1.1"))
        self.assertLess(flush, add)                      # a flush after "addr add" deletes the subnet route the gateway needs
        self.assertLess(add, default)

    def test_dns_and_search_domain_are_written(self):
        self.run_script("bound", ip="10.0.0.2", mask="8", router="10.0.0.1", dns="1.1.1.1 9.9.9.9", domain="lan")
        with open(os.path.join(self.dir, "run", "resolv.conf")) as f:
            self.assertEqual(f.read().split("\n")[:3], ["nameserver 1.1.1.1", "nameserver 9.9.9.9", "search lan"])


if __name__ == "__main__":
    unittest.main()
