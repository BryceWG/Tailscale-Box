#!/system/bin/sh
M=${0%/*}
D=/data/adb/tailscale-direct
SOCK=$D/tailscaled.sock
PIDFILE=$D/taildrop.pid
DEST=/storage/emulated/0/Download/taildrop
mkdir -p "$D" || exit 1
chmod 700 "$D"

alive() {
 [ -s "$PIDFILE" ] || return 1
 P=$(cat "$PIDFILE")
 case "$P" in ''|*[!0-9]*) return 1;; esac
 [ -r "/proc/$P/cmdline" ] || return 1
 CMD=$(tr '\000' ' ' < "/proc/$P/cmdline")
 case "$CMD" in
  "$M/bin/tailscale --socket=$SOCK file get --loop --conflict=rename $DEST "|"/system/bin/sh $M/taildrop.sh receive ") kill -0 "$P" 2>/dev/null;;
  *) return 1;;
 esac
}

start() {
 [ -x "$M/bin/tailscale" ] || { echo 'Taildrop receiver: Tailscale CLI is not installed'; return 1; }
 [ -S "$SOCK" ] || { echo 'Taildrop receiver: tailscaled socket is not ready'; return 1; }
 if alive; then echo 'Taildrop receiver already running'; return 0; fi
 rm -f "$PIDFILE"
 nohup /system/bin/sh "$M/taildrop.sh" receive >> "$D/taildrop.log" 2>&1 < /dev/null &
 echo $! > "$PIDFILE"
 sleep 1
 if alive; then echo "Taildrop receiver started; destination $DEST (waits for shared storage)"; return 0; fi
 rm -f "$PIDFILE"
 echo "Taildrop receiver exited; inspect $D/taildrop.log"
 return 1
}

prepare() {
 # Never create children below a missing shared-storage mount. Checking the
 # actual target is more reliable than testing write access on the FUSE root.
 [ -d /storage/emulated/0 ] || { echo 'Taildrop: shared storage is not mounted'; return 1; }
 umask 022
 mkdir -p "$DEST" || return 1
 [ -d "$DEST" ] && [ -w "$DEST" ] || { echo "Taildrop: destination is not writable: $DEST"; return 1; }
}

receive() {
 # Shared storage may not be mounted until the primary user unlocks. Do not
 # block the network service while waiting, or create directories under an
 # unmounted /storage path. A later ctl.sh start retries if the wait expires.
 WAIT=0
 until prepare > "$D/taildrop-prepare.log" 2>&1; do
  WAIT=$((WAIT + 1))
  [ "$WAIT" -lt 120 ] || {
   cat "$D/taildrop-prepare.log"
   echo 'Taildrop: destination unavailable; unlock and run ctl.sh start'
   return 1
  }
  sleep 5
 done
 # The CLI creates ordinary readable files (0644), never overwrites on conflict,
 # and deletes a completed inbox entry only after the destination copy succeeds.
 echo "Taildrop: receiving into $DEST"
 exec "$M/bin/tailscale" --socket="$SOCK" file get --loop --conflict=rename "$DEST"
}

stop() {
 if alive; then
  kill -TERM "$P" || return 1
  WAIT=0
  while alive; do
   WAIT=$((WAIT + 1))
   [ "$WAIT" -lt 10 ] || { echo 'Taildrop receiver is still stopping'; return 1; }
   sleep 1
  done
 fi
 rm -f "$PIDFILE"
}

case "$1" in
 start|stop)
  mkdir "$D/taildrop.lock" 2>/dev/null || { echo 'Taildrop control busy'; exit 1; }
  trap 'rmdir "$D/taildrop.lock"' EXIT;;
esac
case "$1" in
 start) start;;
 stop) stop;;
 status) alive && echo "Taildrop receiver is running; destination $DEST" || { echo 'Taildrop receiver is stopped'; exit 1; };;
 receive) receive;;
 prepare) prepare;;
 *) echo 'Usage: taildrop.sh start|stop|status|prepare'; exit 1;;
esac
