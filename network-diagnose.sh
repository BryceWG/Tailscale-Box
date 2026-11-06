#!/system/bin/sh
# Read-only report for Linux tailscaled running under Android policy routing.
M=${TS_MODULE_DIR:-${0%/*}}
D=/data/adb/tailscale-direct
SOCK=$D/tailscaled.sock
collect() {
 echo '=== Android and Tailscale ==='
 getprop ro.build.version.release
 getprop ro.build.version.sdk
 "$M/bin/tailscaled" --version
 echo '=== Proxy configured ==='
 if [ -s "$D/control-proxy" ]; then echo yes; else echo no; fi
 echo '=== Daemon socket mode ==='
 if [ -f "$D/tailscaled.pid" ]; then
  p=$(cat "$D/tailscaled.pid")
  case "$p" in ''|*[!0-9]*) ;; *)
   if [ "$(readlink "/proc/$p/exe")" = "$M/bin/tailscaled" ]; then
    tr '\000' '\n' < "/proc/$p/environ" | grep '^TS_FORCE_LINUX_BIND_TO_DEVICE=' || echo 'TS_FORCE_LINUX_BIND_TO_DEVICE unset (upstream default)'
   fi;;
  esac
 fi
 echo '=== Status (text) ==='
 timeout 10 "$M/bin/tailscale" --socket="$SOCK" status
 echo '=== Status JSON (includes Active/CurAddr/PeerRelay) ==='
 timeout 10 "$M/bin/tailscale" --socket="$SOCK" status --json
 echo '=== Netcheck ==='
 timeout 30 "$M/bin/tailscale" --socket="$SOCK" netcheck
 echo '=== IPv4 rules ==='
 ip -4 rule show
 echo '=== IPv6 rules ==='
 ip -6 rule show
 echo '=== IPv4 routes, all tables ==='
 ip -4 route show table all
 echo '=== IPv6 routes, all tables ==='
 ip -6 route show table all
 echo '=== Unmarked vs Linux bypass-mark route (lookup only, no packets sent) ==='
 ip -4 route get 1.1.1.1 uid 0
 ip -4 route get 1.1.1.1 mark 0x80000 uid 0
 echo '=== UDP sockets ==='
 ss -u -a -n -p -e
 if [ -n "$1" ]; then
  echo '=== Repeated peer ping (allow time to discover direct/peer-relay paths) ==='
  timeout 25 "$M/bin/tailscale" --socket="$SOCK" ping --c 10 --timeout=2s "$1"
 fi
 echo '=== Recent network-related daemon log ==='
 tail -n 300 "$D/daemon.log" | grep -iE 'magicsock|netcheck|disco|relay|endpoint|bind|network is unreachable|permission denied|operation not permitted|default.route|health'
}
if [ "$#" -gt 1 ]; then echo 'Usage: network-diagnose.sh [peer-IP]'; exit 1; fi
if [ "$#" -eq 1 ]; then
 case "$1" in ''|*[!0-9a-fA-F:.]*) echo 'Expected literal peer IP'; exit 1;; esac
 [ "${#1}" -le 45 ] || exit 1
fi
collect "$1" 2>&1 | sed 's#https://login\.tailscale\.com/[^[:space:]"<>]*#[login URL redacted]#g; s#https://controlplane\.tailscale\.com/[^[:space:]"<>]*#[login URL redacted]#g'
