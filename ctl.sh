#!/system/bin/sh
MODDIR=${0%/*}
DATA=/data/adb/tailscale-direct
SOCK=$DATA/tailscaled.sock
PIDFILE=$DATA/tailscaled.pid
mkdir -p "$DATA"
chmod 700 "$DATA"
if [ -s "$DATA/control-proxy" ]; then
 TS_PROXY=$(cat "$DATA/control-proxy")
 case "$TS_PROXY" in
  http://127.0.0.1:*|http://localhost:*)
   export HTTP_PROXY="$TS_PROXY" HTTPS_PROXY="$TS_PROXY"
   export NO_PROXY=localhost,127.0.0.1,::1;;
  *) echo 'Invalid control-proxy: expected http://127.0.0.1:PORT'; exit 1;;
 esac
fi
cli() { "$MODDIR/bin/tailscale" --socket="$SOCK" "$@"; }
alive() {
 [ -f "$PIDFILE" ] || return 1
 TS_PID=$(cat "$PIDFILE")
 case "$TS_PID" in ''|*[!0-9]*) return 1;; esac
 [ "$(readlink /proc/$TS_PID/exe)" = "$MODDIR/bin/tailscaled" ] || return 1
 kill -0 "$TS_PID" 2>/dev/null
}
clean_rules() {
 while ip -4 rule del priority 70 to 100.64.0.0/10 table 52 2>/dev/null; do :; done
 while ip -6 rule del priority 70 to fd7a:115c:a1e0::/48 table 52 2>/dev/null; do :; done
}
routes() {
 ip -4 route show table 52 | grep -q tailscale0 || { echo 'No native route in table 52; inspect daemon.log'; return 1; }
 for TS_FAMILY in -4 -6; do
  TS_RULE=$(ip "$TS_FAMILY" rule show | grep '^70:' | grep -v 'to 100.64.0.0/10 lookup 52' | grep -v 'to fd7a:115c:a1e0::/48 lookup 52')
  [ -z "$TS_RULE" ] || { echo "Priority 70 occupied: $TS_RULE"; return 1; }
 done
 clean_rules
 ip -4 rule add priority 70 to 100.64.0.0/10 table 52 || return 1
 if ip -6 route show table 52 | grep -q tailscale0; then
  ip -6 rule add priority 70 to fd7a:115c:a1e0::/48 table 52 || return 1
 fi
}
start() {
 [ -x "$MODDIR/bin/tailscale" ] && [ -x "$MODDIR/bin/tailscaled" ] || { echo "Tailscale not installed; run the module Action button first"; return 1; }
 if alive; then echo 'Already running'; return 0; fi
 if pidof tailscaled >/dev/null 2>&1; then echo 'Another tailscaled exists; stop it first'; return 1; fi
 [ -c /dev/net/tun ] || [ -c /dev/tun ] || { echo 'No TUN device'; return 1; }
 ip link show tailscale0 >/dev/null 2>&1 && { echo 'tailscale0 already exists'; return 1; }
 [ ! -f "$DATA/daemon.log" ] || mv "$DATA/daemon.log" "$DATA/daemon.previous.log"
 nohup "$MODDIR/bin/tailscaled" --tun=tailscale0 --state="$DATA/tailscaled.state" --statedir="$DATA" --socket="$SOCK" --port=41641 > "$DATA/daemon.log" 2>&1 < /dev/null &
 echo $! > "$PIDFILE"
 TS_WAIT=0
 until [ -S "$SOCK" ]; do
  alive || { tail -n 30 "$DATA/daemon.log"; return 1; }
  TS_WAIT=$((TS_WAIT + 1))
  [ "$TS_WAIT" -lt 15 ] || { echo 'Daemon not ready'; return 1; }
  sleep 1
 done
 TS_WAIT=0
 until routes; do
  TS_WAIT=$((TS_WAIT + 1))
  [ "$TS_WAIT" -lt 10 ] || { echo 'Native routes not ready; login then repair'; return 0; }
  alive || return 1
  sleep 2
 done
}
stop() {
 clean_rules
 if alive; then
  kill -TERM "$TS_PID" || return 1
  TS_WAIT=0
  while alive; do
   TS_WAIT=$((TS_WAIT + 1))
   [ "$TS_WAIT" -lt 15 ] || { echo 'Daemon still stopping'; return 1; }
   sleep 1
  done
 fi
 rm -f "$PIDFILE"
}
case "$1" in
 start) start;;
 stop) stop;;
 restart) stop && start;;
 login) alive || { echo 'Start first'; exit 1; }; cli up --timeout=60s --accept-dns=false --accept-routes=false --netfilter-mode=off && routes;;
 repair) alive && routes;;
 status) alive && echo "PID $TS_PID" || echo 'Stopped'; cli status; ip -4 rule show; ip -4 route show table 52;;
 diagnose) "$MODDIR/bin/tailscaled" --version; cli status; cli netcheck; ip -4 route show table 52; ip -6 route show table 52; tail -n 50 "$DATA/daemon.log" | sed 's#https://login\.tailscale\.com/[^[:space:]"<>]*#[login URL redacted]#g; s#https://controlplane\.tailscale\.com/[^[:space:]"<>]*#[login URL redacted]#g';;
 ts) shift; cli "$@";;
 *) echo 'Usage: ctl.sh start|stop|restart|login|repair|status|diagnose|ts <args>'; exit 1;;
esac
