#!/system/bin/sh
MODDIR=${0%/*}
"$MODDIR/ctl.sh" stop
# Keep identity state for deliberate manual removal.
