"""rgos-raguard: kills a RetroArch that stops answering its command port, leaves a healthy one alone."""
import os, socket, subprocess, sys, tempfile, threading, time, unittest

GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay", "opt", "rgos", "rgos-raguard")
if not os.path.exists(GUARD):
    GUARD = "/opt/rgos/rgos-raguard"


class RaGuard(unittest.TestCase):
    def run_guard(self, respond):
        run = tempfile.mkdtemp()
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0)); srv.settimeout(0.2)
        stop = []
        def serve():
            while not stop:
                try:
                    _, a = srv.recvfrom(64)
                except OSError:
                    continue
                if respond:
                    srv.sendto(b"VERSION 1.0", a)
        th = threading.Thread(target=serve); th.start()
        child = subprocess.Popen(["sleep", "30"], start_new_session=True)
        env = dict(os.environ, RGOS_RUN=run, RA_GUARD_PORT=str(srv.getsockname()[1]), RA_GUARD_GRACE="1",
                   RA_GUARD_PERIOD="0.3", RA_GUARD_MAX_MISS="3")
        g = subprocess.Popen([sys.executable, "-I", GUARD, str(child.pid)], env=env, stdout=subprocess.DEVNULL)
        if respond:
            time.sleep(3); child.kill()
        rc = g.wait(timeout=20); stop.append(1); th.join()
        hung = os.path.exists(os.path.join(run, "ra-hung"))
        child.wait(timeout=5)
        return rc, hung

    def test_healthy_retroarch_is_left_alone(self):
        self.assertEqual(self.run_guard(True), (0, False))

    def test_hung_retroarch_is_killed_and_flagged(self):
        self.assertEqual(self.run_guard(False), (3, True))


if __name__ == "__main__":
    unittest.main()
