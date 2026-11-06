#!/system/bin/sh
# Fixed manager bridge. stdout is exclusively JSON; no network listener.
M=${0%/*}
D=/data/adb/tailscale-direct
umask 077
mkdir -p "$D" || exit 1
T=$(mktemp -d "$D/webui.XXXXXX") || exit 1
trap 'rm -rf "$T"' EXIT
json_file() {
 awk 'BEGIN {printf "\""} {if (NR>1) printf "\\n"; for(i=1;i<=length($0);i++){c=substr($0,i,1); if(c=="\\") printf "\\\\"; else if(c=="\"") printf "\\\""; else if(c=="\t") printf "\\t"; else if(c=="\r") printf "\\r"; else {found=0;for(n=1;n<32;n++)if(c==sprintf("%c",n)){printf "\\u%04x",n;found=1;break}if(!found)printf "%s",c}}} END {printf "\""}' "$1"
}
installed=false
[ ! -x "$M/bin/tailscale" ] || [ ! -x "$M/bin/tailscaled" ] || installed=true
running=false
if [ -f "$D/tailscaled.pid" ]; then
 p=$(cat "$D/tailscaled.pid")
 case "$p" in ''|*[!0-9]*) ;; *)
  if [ "$(readlink "/proc/$p/exe")" = "$M/bin/tailscaled" ] && kill -0 "$p" 2>/dev/null; then running=true; fi;;
 esac
fi
case "$1" in
 check-update)
  : > "$T/current"; : > "$T/latest"; : > "$T/output"
  if $installed; then "$M/bin/tailscale" version 2>/dev/null | head -n 1 > "$T/current"; fi
  # Same optional local control proxy used by the updater, without changing files.
  if [ -s "$D/control-proxy" ]; then
   proxy=$(cat "$D/control-proxy")
   case "$proxy" in
    http://127.0.0.1:*|http://localhost:*) export http_proxy="$proxy" https_proxy="$proxy" HTTP_PROXY="$proxy" HTTPS_PROXY="$proxy";;
    *) printf '{"current":"","latest":"","available":false,"output":"Invalid control-proxy","exitCode":1}\n'; exit 0;;
   esac
  fi
  curl -fsSL --connect-timeout 10 --max-time 20 'https://pkgs.tailscale.com/stable/?mode=json' -o "$T/meta" 2> "$T/output"
  rc=$?
  available=false
  if [ "$rc" -eq 0 ]; then
   grep -oE 'tailscale_[0-9]+\.[0-9]+\.[0-9]+_arm64\.tgz' "$T/meta" | head -n 1 | sed 's/^tailscale_//; s/_arm64\.tgz$//' > "$T/latest"
   if [ -s "$T/latest" ]; then
    current=$(cat "$T/current"); latest=$(cat "$T/latest")
    available=$(awk -v current="$current" -v latest="$latest" 'BEGIN {
     if(current==""){print "true";exit}
     split(current,c,".");split(latest,l,".")
     for(i=1;i<=3;i++){if(l[i]+0>c[i]+0){print "true";exit}if(l[i]+0<c[i]+0){print "false";exit}}
     print "false"
    }')
    printf 'Installed-%s\nStable-%s\n' "$current" "$latest" > "$T/output"
   else rc=1; echo 'Invalid stable ARM64 metadata' > "$T/output"; fi
  fi
  printf '{"current":'; json_file "$T/current"
  printf ',"latest":'; json_file "$T/latest"
  printf ',"available":%s,"exitCode":%s,"output":' "$available" "$rc"; json_file "$T/output"
  printf '}\n';;
 ping)
  # Literal IP only, one request per invocation; never take the mutation lock.
  ip=$2
  case "$ip" in
   ''|*[!0-9a-fA-F:.]*) valid=false;;
   *:*|*.*) valid=true;;
   *) valid=false;;
  esac
  if [ "$#" -ne 2 ] || [ "${#ip}" -gt 45 ] || ! $valid; then
   printf '{"output":"Invalid IP address","exitCode":1}\n'; exit 0
  fi
  if ! $installed; then
   printf '{"output":"Tailscale is not installed","exitCode":1}\n'; exit 0
  fi
  # The CLI sleeps one second after each pong, even with --c 1. Use its
  # underlying Unix-socket API to return the measured path immediately.
  curl -fsS --noproxy '*' --connect-timeout 1 --max-time 3 \
   --unix-socket "$D/tailscaled.sock" \
   --data-urlencode "ip=$ip" --data 'type=disco' \
   http://local-tailscaled.sock/localapi/v0/ping > "$T/result" 2> "$T/output"
  rc=$?
  printf '{"exitCode":%s,"output":' "$rc"; json_file "$T/output"
  printf ',"result":'
  if [ "$rc" -eq 0 ]; then cat "$T/result"; else printf null; fi
  printf '}\n';;
 status)
  printf '{"installed":%s,"running":%s,"version":' "$installed" "$running"
  if $installed; then "$M/bin/tailscale" version 2>/dev/null | head -n 1 > "$T/version"; else : > "$T/version"; fi
  json_file "$T/version"
  printf ',"status":'
  if $installed && $running; then
   timeout 10 "$M/ctl.sh" ts status --json > "$T/status" 2> "$T/error"
   if grep -q '^[[:space:]]*{' "$T/status"; then cat "$T/status"; else printf null; fi
  else printf null; fi
  printf '}\n';;
 login|netcheck|start|stop|repair|update)
  command=$1
  if [ "$command" != update ] && ! $installed; then
   printf '{"output":"Install Tailscale using the module Action button first.","exitCode":1}\n'; exit 0
  fi
  mkdir "$D/webui.lock" 2>/dev/null || { printf '{"output":"Another WebUI operation is running.","exitCode":1}\n'; exit 0; }
  trap 'rm -rf "$T"; rmdir "$D/webui.lock"' EXIT
  case "$command" in
   login) "$M/ctl.sh" login > "$T/output" 2>&1;;
   netcheck) timeout 30 "$M/ctl.sh" ts netcheck > "$T/output" 2>&1;;
   update) "$M/update.sh" > "$T/output" 2>&1;;
   *) "$M/ctl.sh" "$command" > "$T/output" 2>&1;;
  esac
  rc=$?
  printf '{"exitCode":%s,"output":' "$rc"; json_file "$T/output"
  if [ "$command" = login ]; then
   grep -oE 'https://(login|controlplane)\.tailscale\.com/[^[:space:]"<>]*' "$T/output" | head -n 1 > "$T/url"
   printf ',"url":'; json_file "$T/url"
  fi
  printf '}\n';;
 *) printf '{"output":"Unknown operation","exitCode":1}\n';;
esac
