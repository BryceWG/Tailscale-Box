#!/system/bin/sh
MODDIR=${0%/*}
until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 3; done
[ -f "$MODDIR/disable" ] && exit 0
mkdir -p /data/adb/tailscale-direct
chmod 700 /data/adb/tailscale-direct
[ -x "$MODDIR/bin/tailscaled" ] || exit 0
"$MODDIR/ctl.sh" start >> /data/adb/tailscale-direct/boot.log 2>&1
