################################################################################
#
# rgos-libretro-mgba
#
# mGBA libretro core for RG OS. Named rgos-* on purpose so it can never clash
# with a libretro-mgba package that a Buildroot tree may or may not ship.
#
################################################################################

RGOS_LIBRETRO_MGBA_VERSION = $(call qstrip,$(BR2_PACKAGE_RGOS_LIBRETRO_MGBA_VERSION))
RGOS_LIBRETRO_MGBA_SITE = $(call github,libretro,mgba,$(RGOS_LIBRETRO_MGBA_VERSION))
RGOS_LIBRETRO_MGBA_LICENSE = MPL-2.0
RGOS_LIBRETRO_MGBA_LICENSE_FILES = LICENSE
RGOS_LIBRETRO_MGBA_DEPENDENCIES = rgos-retroarch

# platform=unix selects the plain Linux shared-object build. It must be given
# explicitly: Makefile.libretro otherwise guesses from `uname` of the BUILD
# host, which is x86_64 when cross-compiling. TARGET_CONFIGURE_OPTS passes the
# cross CC/CXX/CFLAGS/LDFLAGS (including -mcpu=cortex-a53 from the defconfig).
define RGOS_LIBRETRO_MGBA_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) -f Makefile.libretro \
		$(TARGET_CONFIGURE_OPTS) \
		platform=unix \
		GIT_VERSION="-$(RGOS_LIBRETRO_MGBA_VERSION)"
endef

define RGOS_LIBRETRO_MGBA_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/mgba_libretro.so \
		$(TARGET_DIR)/usr/lib/libretro/mgba_libretro.so
	$(INSTALL) -D -m 0644 $(RGOS_LIBRETRO_MGBA_PKGDIR)/mgba_libretro.info \
		$(TARGET_DIR)/usr/share/libretro/info/mgba_libretro.info
endef

$(eval $(generic-package))
