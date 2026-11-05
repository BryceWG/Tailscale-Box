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
