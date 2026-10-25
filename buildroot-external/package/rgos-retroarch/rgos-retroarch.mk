################################################################################
#
# rgos-retroarch
#
# RetroArch frontend for RG OS. Buildroot has no retroarch package (it only
# exists in Batocera/Recalbox/Lakka), so it is built from the upstream
# tarball. RetroArch uses its own hand-written ./configure (not autotools).
#
################################################################################

RGOS_RETROARCH_VERSION = $(call qstrip,$(BR2_PACKAGE_RGOS_RETROARCH_VERSION))
RGOS_RETROARCH_SITE = $(call github,libretro,RetroArch,$(RGOS_RETROARCH_VERSION))
RGOS_RETROARCH_LICENSE = GPL-3.0+
RGOS_RETROARCH_LICENSE_FILES = COPYING
RGOS_RETROARCH_DEPENDENCIES = host-pkgconf libdrm mesa3d alsa-lib sdl2 zlib

# Only what the device has: KMS/DRM + EGL + GLES3 video, ALSA audio, SDL2 input.
# Everything else (X11, Wayland, Vulkan, Qt, ffmpeg, networking, udev, ...) is off
# so no extra libraries have to be in the initramfs.
RGOS_RETROARCH_CONF_OPTS = \
	--prefix=/usr \
	--disable-x11 --disable-wayland --disable-vulkan --disable-qt \
	--disable-pulse --disable-oss --disable-jack --disable-ffmpeg \
	--disable-networking --disable-udev --disable-dbus --disable-v4l2 \
	--disable-freetype --disable-discord --disable-cdrom \
	--disable-online_updater --disable-update_assets \
	--disable-materialui --disable-xmb --disable-ozone \
	--disable-sdl --enable-sdl2 --enable-alsa --enable-zlib \
	--enable-kms --enable-egl --enable-opengles --enable-opengles3

# No X11 on the device. Without EGL_NO_X11, Mesa's EGL/eglplatform.h includes <X11/Xlib.h> on Linux, so RetroArch's configure
# header probe for EGL/eglext.h fails ("Build assumed that EGL/egl.h exists, but cannot locate"). Passing the define through CFLAGS
# did not reach that probe (the CI build of Mesa 26.2.5 still failed with it), so it is also set inside the staged header itself.
RGOS_RETROARCH_EGL_CFLAGS = -DEGL_NO_X11 -DMESA_EGL_NO_X11_HEADERS
RGOS_RETROARCH_FLAGS = \
	CFLAGS="$(TARGET_CFLAGS) $(RGOS_RETROARCH_EGL_CFLAGS)" \
	CXXFLAGS="$(TARGET_CXXFLAGS) $(RGOS_RETROARCH_EGL_CFLAGS)"

define RGOS_RETROARCH_EGL_NO_X11_HEADER
	f=$(STAGING_DIR)/usr/include/EGL/eglplatform.h; \
	if [ -f "$$f" ] && ! grep -q RGOS_EGL_NO_X11 "$$f"; then \
		{ printf '/* RGOS_EGL_NO_X11: no X11 on the device */\n#ifndef EGL_NO_X11\n#define EGL_NO_X11 1\n#endif\n#ifndef MESA_EGL_NO_X11_HEADERS\n#define MESA_EGL_NO_X11_HEADERS 1\n#endif\n'; cat "$$f"; } > "$$f.rgos" && mv "$$f.rgos" "$$f"; \
	fi
endef
RGOS_RETROARCH_PRE_CONFIGURE_HOOKS += RGOS_RETROARCH_EGL_NO_X11_HEADER

# If configure still fails, print the end of its config.log (the failing compiler command and error) so the CI log shows the real cause.
define RGOS_RETROARCH_CONFIGURE_CMDS
	cd $(@D) && $(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(RGOS_RETROARCH_FLAGS) \
		PKG_CONF_PATH="$(PKG_CONFIG_HOST_BINARY)" \
		./configure $(RGOS_RETROARCH_CONF_OPTS) || { \
			echo "==== RetroArch configure failed; tail of config.log ===="; \
			tail -n 150 $(@D)/config.log 2>/dev/null || ls -la $(@D); \
			echo "==== EGL headers in staging ===="; \
			ls -la $(STAGING_DIR)/usr/include/EGL $(STAGING_DIR)/usr/include/KHR 2>&1; \
			exit 1; }
endef

define RGOS_RETROARCH_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) $(TARGET_CONFIGURE_OPTS) $(RGOS_RETROARCH_FLAGS) \
		PKG_CONF_PATH="$(PKG_CONFIG_HOST_BINARY)"
endef

# Binary only: the menu is RGUI (built in), the config lives in /etc/rgos.
define RGOS_RETROARCH_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/retroarch $(TARGET_DIR)/usr/bin/retroarch
endef

$(eval $(generic-package))
