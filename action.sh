#!/system/bin/sh
MODDIR=${0%/*}
DATA=/data/adb/tailscale-direct
OUT=$DATA/action.log
mkdir -p "$DATA"
chmod 700 "$DATA"
# Keep this invocation's exit code even when tee succeeds or another update is busy.
RESULT=$(mktemp "$DATA/action-exit.XXXXXX") || exit 1
trap 'rm -f "$RESULT"' EXIT
echo Action-started > "$OUT"
{ /system/bin/sh "$MODDIR/update.sh"; echo "$?" > "$RESULT"; } 2>&1 | tee -a "$OUT"
RC=$(cat "$RESULT")
echo "Action-finished-exit-$RC" >> "$OUT"
exit "$RC"
