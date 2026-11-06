#!/system/bin/sh
[ "$BOOTMODE" = true ] || abort "Install from root manager"
[ "$ARCH" = arm64 ] || abort "ARM64 only"
[ ! -d /data/adb/modules/magisk-tailscaled ] || abort "Remove old module and reboot first"
mkdir -p /data/adb/tailscale-direct
chmod 700 /data/adb/tailscale-direct
# Preserve downloaded binaries on an upgrade to the minimal package.
OLD=/data/adb/modules/tailscale_direct
if [ ! -f "$MODPATH/bin/tailscale" ] && [ -x "$OLD/bin/tailscale" ] && [ -x "$OLD/bin/tailscaled" ]; then
 mkdir -p "$MODPATH/bin"
 cp -p "$OLD/bin/tailscale" "$OLD/bin/tailscaled" "$MODPATH/bin/" || abort "Cannot preserve installed Tailscale"
fi
for SCRIPT in ctl.sh update.sh action.sh webui.sh network-diagnose.sh taildrop.sh service.sh uninstall.sh; do
 set_perm "$MODPATH/$SCRIPT" 0 0 0755
done
if [ -f "$MODPATH/bin/tailscale" ] && [ -f "$MODPATH/bin/tailscaled" ]; then
 set_perm "$MODPATH/bin/tailscale" 0 0 0755
 set_perm "$MODPATH/bin/tailscaled" 0 0 0755
 ui_print "Tailscale binaries ready"
else
 ui_print "Minimal package: use Action or WebUI to install Tailscale"
fi
# webroot permissions and contexts are managed by the WebUI host.
if /system/bin/sh "$MODPATH/taildrop.sh" prepare >> /data/adb/tailscale-direct/taildrop.log 2>&1; then
 ui_print "Taildrop folder ready: Download/taildrop"
else
 ui_print "Taildrop folder will be created after shared storage is ready"
fi
ui_print "Tailscale Box installed"
ui_print "WebUI: KernelSU/APatch or compatible WebUI host"
