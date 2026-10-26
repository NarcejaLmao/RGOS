"""Framebuffer output for RG OS (used when SDL's kmsdrm video driver cannot start; always on the TrimUI Smart Pro).

Renders a pygame Surface straight into /dev/fb0 (the Linux fbdev interface; on the TSP it is the Allwinner BSP `disp2`
framebuffer, 1280x720), so the menu shows up even without SDL/EGL/GBM or a GPU driver. Also switches tty1 into graphics
mode so the kernel console stops drawing text over the menu.

Features (all optional, each falls back silently if the driver refuses):
  * aspect-preserving fit: the menu canvas (720x720, 1:1, for the RG Rotate's square screen) is scaled uniformly into the screen, never stretched; a square panel is filled exactly, anything else gets black bars
  * double buffering through pan (FBIOPUT_VSCREENINFO yres_virtual = 2*yres, FBIOPAN_DISPLAY) + FBIO_WAITFORVSYNC
  * skips frames that did not change (the menu redraws at 30 fps, but mostly shows a static screen)
  * RGOS_FB_ROTATE=0|90|180|270 (degrees, clockwise) for panels whose native scan-out is rotated
  * RGOS_FB_DEV=/dev/fbN, RGOS_FB_DBUF=0 (force single buffer), RGOS_FB_VSYNC=0
"""
import fcntl, mmap, os, struct, zlib

FBIOGET_VSCREENINFO, FBIOPUT_VSCREENINFO, FBIOGET_FSCREENINFO, FBIOPAN_DISPLAY = 0x4600, 0x4601, 0x4602, 0x4606
FBIO_WAITFORVSYNC = 0x40044620
KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3A, 0, 1


def set_graphics(on):
    """Stop (on=True) / resume (on=False) the kernel text console on tty1."""
    try:
        fd = os.open("/dev/tty1", os.O_RDWR | os.O_NOCTTY)
        try:
            fcntl.ioctl(fd, KDSETMODE, KD_GRAPHICS if on else KD_TEXT)
        finally:
            os.close(fd)
    except OSError:
        pass


def fit_rect(src, dst):
    """Largest rectangle (x, y, w, h) with the aspect ratio of `src` (w, h) centred inside `dst` (w, h)."""
    sw, sh = src
    dw, dh = dst
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return (0, 0, max(dw, 0), max(dh, 0))
    if abs(dw * sh - dh * sw) * 200 <= dw * sh:      # within 0.5 %: fill the screen (854x480 canvas on a 1280x720 panel)
        return (0, 0, dw, dh)
    if dw * sh >= dh * sw:            # screen is wider than the source: bars left/right
        h, w = dh, max(1, dh * sw // sh)
    else:                             # screen is narrower: bars top/bottom
        w, h = dw, max(1, dw * sh // sw)
    return ((dw - w) // 2, (dh - h) // 2, w, h)


class FbOut:
    def __init__(self, dev=None):
        import pygame
        self.pg = pygame
        dev = dev or os.environ.get("RGOS_FB_DEV", "/dev/fb0")
        self.fd = os.open(dev, os.O_RDWR)
        self.rotate = int(os.environ.get("RGOS_FB_ROTATE", "0") or 0) % 360
        v = self._get_var()
        self.xres, self.yres = v[0], v[1]
        self.bpp = v[6]
        if self.bpp not in (16, 24, 32):
            raise RuntimeError("unsupported framebuffer depth %d" % self.bpp)
        # try to get a second page for double buffering
        self.pages = 1
        if os.environ.get("RGOS_FB_DBUF", "1") != "0":
            try:
                vv = self._raw_var()
                struct.pack_into("<2I", vv, 8, self.xres, self.yres * 2)       # xres_virtual, yres_virtual
                fcntl.ioctl(self.fd, FBIOPUT_VSCREENINFO, vv)
                if self._get_var()[3] >= self.yres * 2:
                    self.pages = 2
            except OSError:
                pass
        v = self._get_var()
        self.xvirt, self.yvirt, self.xoff, self.yoff = v[2], v[3], v[4], v[5]
        raw = self._raw_var()
        ro, rl, _, go, gl, _, bo, bl, _, to, tl, _ = struct.unpack_from("<12I", raw, 32)
        f = bytearray(80)
        fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, f)
        smem_len = struct.unpack_from("<I", f, 24)[0]
        self.line = struct.unpack_from("<I", f, 48)[0]
        self.mm = mmap.mmap(self.fd, smem_len)
        # The TSP's BSP driver (rotated panel, G2D copy) shows data that arrives through write() but did NOT show what was
        # stored through the mmap (log: menu running, screen stuck on the red write()-painted marker). So write() by default;
        # RGOS_FB_MMAP=1 goes back to mmap.
        self.use_write = os.environ.get("RGOS_FB_MMAP", "0") != "1"
        self.bytes_pp = self.bpp // 8
        if self.pages == 2 and self.line * self.yres * 2 > smem_len:
            self.pages = 1                                  # driver accepted the size but has no memory for it
        mask = lambda o, l: ((1 << l) - 1) << o
        masks = (mask(ro, rl), mask(go, gl), mask(bo, bl), mask(to, tl) if tl else 0)
        self.surf = pygame.Surface((self.xres, self.yres), 0, self.bpp, masks)
        self.vsync = os.environ.get("RGOS_FB_VSYNC", "1") != "0"
        self.page = 0
        self.last = None
        self.cache_key = None
        self.cache = None
        self.info = "fb %dx%d %dbpp x%d page%s rot%d" % (self.xres, self.yres, self.bpp, self.pages,
                                                         "s" if self.pages > 1 else "", self.rotate)

    # -- ioctl helpers ------------------------------------------------------------------------------------------
    def _raw_var(self):
        v = bytearray(160)
        fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, v)
        return v

    def _get_var(self):
        return struct.unpack_from("<7I", self._raw_var(), 0)

    def _flip(self, page):
        """Show `page` (pan the display) and wait for vertical blank where the driver supports it."""
        if self.pages < 2:
            try:
                vv = self._raw_var()
                struct.pack_into("<2I", vv, 16, 0, 0)
                fcntl.ioctl(self.fd, FBIOPAN_DISPLAY, vv)       # some BSP drivers only refresh the panel on a pan
            except OSError:
                pass
            if self.vsync:
                try:
                    fcntl.ioctl(self.fd, FBIO_WAITFORVSYNC, struct.pack("<I", 0))
                except OSError:
                    self.vsync = False
            return
        try:
            vv = self._raw_var()
            struct.pack_into("<2I", vv, 16, 0, page * self.yres)      # xoffset, yoffset
            fcntl.ioctl(self.fd, FBIOPAN_DISPLAY, vv)
            if self.vsync:
                try:
                    fcntl.ioctl(self.fd, FBIO_WAITFORVSYNC, struct.pack("<I", 0))
                except OSError:
                    self.vsync = False
        except OSError:
            self.pages = 1                                  # pan not supported after all: single buffer from now on

    # -- drawing ------------------------------------------------------------------------------------------------
    def _put(self, off, data):
        if self.use_write:
            try:
                n = 0
                while n < len(data):
                    n += os.pwrite(self.fd, data[n:], off + n)
                return
            except OSError:
                self.use_write = False                        # driver refuses write(): fall back to mmap
        self.mm[off:off + len(data)] = data

    def clear(self):
        self._put(0, b"\0" * len(self.mm))
        self.last = None

    def _scaled(self, canvas):
        """canvas scaled (aspect preserved) and rotated, as a surface of exactly the screen size, black bars included."""
        pg = self.pg
        src = canvas
        if self.rotate:
            src = pg.transform.rotate(src, -self.rotate)
        sw, sh = src.get_size()
        key = (sw, sh)
        if self.cache_key != key:
            self.cache_key = key
            self.rect = fit_rect(key, (self.xres, self.yres))
            self.cache = pg.Surface((self.xres, self.yres), 0, 32)
        x, y, w, h = self.rect
        if (w, h) == (sw, sh):
            self.cache.blit(src, (x, y))
        else:
            self.cache.blit(pg.transform.smoothscale(src, (w, h)) if src.get_bitsize() in (24, 32)
                            else pg.transform.scale(src, (w, h)), (x, y))
        return self.cache

    def present(self, canvas):
        # unchanged frame -> nothing to do (adler32 of 1.4 MB takes ~1 ms)
        h = zlib.adler32(canvas.get_buffer().raw)
        if h == self.last:
            return
        self.last = h
        self.surf.blit(self._scaled(canvas), (0, 0))
        raw = self.surf.get_buffer().raw
        pitch, rowlen = self.surf.get_pitch(), self.xres * self.bytes_pp
        back = (1 - self.page) if self.pages > 1 else 0          # draw into the page that is not on screen
        base = back * self.yres * self.line + self.xoff * self.bytes_pp
        if self.pages < 2:
            base += self.yoff * self.line
        if pitch == rowlen == self.line:
            self._put(base, raw[:rowlen * self.yres])
        else:
            for y in range(self.yres):
                self._put(base + y * self.line, raw[y * pitch:y * pitch + rowlen])
        self.page = back
        self._flip(back)
