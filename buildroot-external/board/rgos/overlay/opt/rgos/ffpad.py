"""ffpad - force-feedback (rumble) support for the rgos-sticks virtual pad.

RetroArch (udev driver) and SDL2 (haptics) rumble a gamepad by uploading an FF_RUMBLE effect to its evdev node and
playing it. For a uinput device the kernel hands those requests to the process that created it, which is the input
hub. FFHandler answers them and drives the vibration motor (hwctl.Motor, an on/off motor: strong and weak magnitude
are folded into one level, on above `threshold`).

Protocol (linux/uinput.h): the hub reads struct input_event records from the uinput fd. EV_UINPUT/UI_FF_UPLOAD and
UI_FF_ERASE carry a request id that must be answered with UI_BEGIN_* / UI_END_* ioctls (the caller of EVIOCSFF blocks
until then); EV_FF events with code = effect id and value = repeat count (0 = stop) play and stop effects.
"""
import ctypes, fcntl, os, struct, time

EV_FF, EV_UINPUT = 0x15, 0x0101
UI_FF_UPLOAD, UI_FF_ERASE = 1, 2
FF_RUMBLE = 0x50
EVFMT = struct.Struct("llHHi")


class _Trigger(ctypes.Structure):
    _fields_ = [("button", ctypes.c_uint16), ("interval", ctypes.c_uint16)]


class _Replay(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint16), ("delay", ctypes.c_uint16)]


class _Envelope(ctypes.Structure):
    _fields_ = [("attack_length", ctypes.c_uint16), ("attack_level", ctypes.c_uint16),
                ("fade_length", ctypes.c_uint16), ("fade_level", ctypes.c_uint16)]


class _Constant(ctypes.Structure):
    _fields_ = [("level", ctypes.c_int16), ("envelope", _Envelope)]


class _Ramp(ctypes.Structure):
    _fields_ = [("start_level", ctypes.c_int16), ("end_level", ctypes.c_int16), ("envelope", _Envelope)]


class _Condition(ctypes.Structure):
    _fields_ = [("right_saturation", ctypes.c_uint16), ("left_saturation", ctypes.c_uint16),
                ("right_coeff", ctypes.c_int16), ("left_coeff", ctypes.c_int16),
                ("deadband", ctypes.c_uint16), ("center", ctypes.c_int16)]


class _Periodic(ctypes.Structure):
    _fields_ = [("waveform", ctypes.c_uint16), ("period", ctypes.c_uint16), ("magnitude", ctypes.c_int16),
                ("offset", ctypes.c_int16), ("phase", ctypes.c_uint16), ("envelope", _Envelope),
                ("custom_len", ctypes.c_uint32), ("custom_data", ctypes.c_void_p)]


class _Rumble(ctypes.Structure):
    _fields_ = [("strong_magnitude", ctypes.c_uint16), ("weak_magnitude", ctypes.c_uint16)]


class _Haptic(ctypes.Structure):
    _fields_ = [("hid_usage", ctypes.c_uint16), ("vendor_id", ctypes.c_uint16), ("vendor_page", ctypes.c_uint8),
                ("pad", ctypes.c_uint8), ("pad2", ctypes.c_uint16)]


class _EffectUnion(ctypes.Union):
    _fields_ = [("constant", _Constant), ("ramp", _Ramp), ("periodic", _Periodic), ("condition", _Condition * 2),
                ("rumble", _Rumble)]


class FFEffect(ctypes.Structure):          # struct ff_effect
    _fields_ = [("type", ctypes.c_uint16), ("id", ctypes.c_int16), ("direction", ctypes.c_uint16),
                ("trigger", _Trigger), ("replay", _Replay), ("u", _EffectUnion)]


class UploadReq(ctypes.Structure):         # struct uinput_ff_upload
    _fields_ = [("request_id", ctypes.c_uint32), ("retval", ctypes.c_int32),
                ("effect", FFEffect), ("old", FFEffect)]


class EraseReq(ctypes.Structure):          # struct uinput_ff_erase
    _fields_ = [("request_id", ctypes.c_uint32), ("retval", ctypes.c_int32),
                ("effect_id", ctypes.c_uint32)]


def _ioc(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


UI_BEGIN_FF_UPLOAD = _ioc(3, "U", 200, ctypes.sizeof(UploadReq))
UI_END_FF_UPLOAD = _ioc(1, "U", 201, ctypes.sizeof(UploadReq))
UI_BEGIN_FF_ERASE = _ioc(3, "U", 202, ctypes.sizeof(EraseReq))
UI_END_FF_ERASE = _ioc(1, "U", 203, ctypes.sizeof(EraseReq))


class FFHandler:
    """Answers the uinput FF handshake on `ufd` and runs the motor. Call service() when ufd is readable and tick()
    regularly (next_deadline() says when the next effect ends)."""

    def __init__(self, ufd, motor, threshold=0.15, max_ms=3000, clock=time.monotonic):
        self.ufd, self.motor, self.threshold, self.max_s, self.clock = ufd, motor, threshold, max_ms / 1000.0, clock
        self.effects = {}          # id -> (level 0..1, length in s or 0 = until stopped)
        self.playing = {}          # id -> deadline (monotonic) or None
        self.motor_on = False

    def fileno(self):
        return self.ufd

    @staticmethod
    def level(strong, weak):
        return max(strong, weak * 0.6) / 65535.0

    def _upload(self, req_id):
        buf = bytearray(ctypes.sizeof(UploadReq))
        up = UploadReq.from_buffer(buf)
        up.request_id = req_id
        fcntl.ioctl(self.ufd, UI_BEGIN_FF_UPLOAD, buf, True)
        e = up.effect
        if e.type == FF_RUMBLE:
            self.effects[e.id] = (self.level(e.u.rumble.strong_magnitude, e.u.rumble.weak_magnitude), e.replay.length / 1000.0)
            up.retval = 0
        else:
            up.retval = -22                     # EINVAL: only rumble is supported
        fcntl.ioctl(self.ufd, UI_END_FF_UPLOAD, buf, True)

    def _erase(self, req_id):
        buf = bytearray(ctypes.sizeof(EraseReq))
        er = EraseReq.from_buffer(buf)
        er.request_id = req_id
        fcntl.ioctl(self.ufd, UI_BEGIN_FF_ERASE, buf, True)
        self.effects.pop(er.effect_id, None)
        self.playing.pop(er.effect_id, None)
        er.retval = 0
        fcntl.ioctl(self.ufd, UI_END_FF_ERASE, buf, True)

    def handle(self, t, code, value):
        if t == EV_UINPUT and code == UI_FF_UPLOAD:
            self._upload(value)
        elif t == EV_UINPUT and code == UI_FF_ERASE:
            self._erase(value)
        elif t == EV_FF:
            if value > 0 and code in self.effects:
                length = self.effects[code][1]
                self.playing[code] = self.clock() + (min(length, self.max_s) if length else self.max_s)
            elif value == 0:
                self.playing.pop(code, None)

    def service(self):
        """Drain the uinput fd (non-blocking)."""
        while True:
            try:
                data = os.read(self.ufd, EVFMT.size * 16)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if not data:
                break
            for off in range(0, len(data) - EVFMT.size + 1, EVFMT.size):
                _, _, t, c, v = EVFMT.unpack_from(data, off)
                try:
                    self.handle(t, c, v)
                except OSError:
                    pass
        self.tick()

    def tick(self):
        now = self.clock()
        for i in [i for i, d in self.playing.items() if d is not None and d <= now]:
            del self.playing[i]
        want = any(self.effects.get(i, (0, 0))[0] >= self.threshold for i in self.playing)
        if want != self.motor_on:
            self.motor.set(want)
            self.motor_on = want

    def next_deadline(self, default=0.25):
        """Seconds until tick() must run again (bounded by default)."""
        if not self.playing:
            return default
        soonest = min(d for d in self.playing.values() if d is not None)
        return max(0.0, min(default, soonest - self.clock()))

    def close(self):
        self.playing.clear()
        self.tick()
