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

define RGOS_RETROARCH_CONFIGURE_CMDS
	cd $(@D) && $(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) \
		PKG_CONF_PATH="$(PKG_CONFIG_HOST_BINARY)" \
		./configure $(RGOS_RETROARCH_CONF_OPTS)
endef

define RGOS_RETROARCH_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) $(TARGET_CONFIGURE_OPTS) \
		PKG_CONF_PATH="$(PKG_CONFIG_HOST_BINARY)"
endef

# Binary only: the menu is RGUI (built in), the config lives in /etc/rgos.
define RGOS_RETROARCH_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/retroarch $(TARGET_DIR)/usr/bin/retroarch
endef

$(eval $(generic-package))
