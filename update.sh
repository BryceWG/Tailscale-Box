#!/system/bin/sh
M=${0%/*}
D=/data/adb/tailscale-direct
W=$D/update.tmp
L=$D/update.lock
U=https://pkgs.tailscale.com/stable
umask 077
mkdir -p "$D"; chmod 700 "$D"
mkdir "$L" 2>/dev/null || { echo 'Updater busy; inspect module Action output'; exit 1; }
rm -f "$D/update.exit"
trap 'RC=$?; echo Update-exit-code-$RC; echo "$RC" > "$D/update.exit"; rm -rf "$L" "$W"; rm -f "$M/bin/tailscale.new" "$M/bin/tailscaled.new"' EXIT
[ ! -s "$D/control-proxy" ] || { P=$(cat "$D/control-proxy"); export http_proxy="$P" https_proxy="$P" HTTP_PROXY="$P" HTTPS_PROXY="$P"; }
for x in curl tar sha256sum awk grep sed head cp mv mkdir chmod; do command -v "$x" >/dev/null 2>&1 || { echo "Missing-$x"; exit 1; }; done
mkdir -p "$W/unpack" "$M/bin" || exit 1
echo Stage-metadata
curl -fsSL --connect-timeout 15 --max-time 90 "$U/?mode=json" -o "$W/meta" || { echo Metadata-failed; exit 1; }
A=$(grep -o 'tailscale_[0-9.]*_arm64.tgz' "$W/meta" | head -n1)
V=$(echo "$A" | cut -d _ -f2)
case "$V" in ''|*[!0-9.]*) echo Invalid-version-metadata; exit 1;; esac
CURRENT=not-installed
if [ -x "$M/bin/tailscale" ] && [ -x "$M/bin/tailscaled" ]; then CURRENT=$("$M/bin/tailscaled" --version 2>/dev/null | head -n1); fi
echo "Installed-$CURRENT"; echo "Stable-$V"; echo "Archive-$A"
[ "$CURRENT" != "$V" ] || { echo Already-current; exit 0; }
echo Stage-download-archive
curl -fL --connect-timeout 15 --max-time 600 --retry 2 --retry-delay 2 "$U/$A" -o "$W/a.tgz" || { echo Archive-download-failed; exit 1; }
echo Stage-download-checksum
curl -fsSL --connect-timeout 15 --max-time 90 "$U/$A.sha256" -o "$W/a.sha" || { echo Checksum-download-failed; exit 1; }
E=$(awk 'NR==1{print $1}' "$W/a.sha"); H=$(sha256sum "$W/a.tgz" | awk '{print $1}')
[ "$E" = "$H" ] || { echo Checksum-mismatch; exit 1; }
echo Stage-extract
tar -xzf "$W/a.tgz" -C "$W/unpack" || { echo Extract-failed; exit 1; }
S=$W/unpack/tailscale_${V}_arm64
[ -f "$S/tailscale" ] && [ -f "$S/tailscaled" ] || { echo Binaries-missing; exit 1; }
cp "$S/tailscale" "$M/bin/tailscale.new" && cp "$S/tailscaled" "$M/bin/tailscaled.new" || exit 1
chmod 755 "$M/bin/tailscale.new" "$M/bin/tailscaled.new" || exit 1
echo Stage-test-binaries
"$M/bin/tailscale.new" version && "$M/bin/tailscaled.new" --version || exit 1
B=$D/backup-$(date +%Y%m%d-%H%M%S)
mkdir -m 700 "$B" || exit 1
# An empty module has nothing to back up. A partial installation is preserved too.
for binary in tailscale tailscaled; do
 [ ! -f "$M/bin/$binary" ] || cp -p "$M/bin/$binary" "$B/" || exit 1
done
WAS_RUNNING=0
if [ -f "$D/tailscaled.pid" ]; then
 PID=$(cat "$D/tailscaled.pid")
 case "$PID" in ''|*[!0-9]*) ;; *) [ "$(readlink "/proc/$PID/exe" 2>/dev/null)" != "$M/bin/tailscaled" ] || WAS_RUNNING=1;; esac
fi
[ ! -f "$D/tailscaled.state" ] || cp -p "$D/tailscaled.state" "$B/" || exit 1
restore() {
 for binary in tailscale tailscaled; do
  if [ -f "$B/$binary" ]; then cp -p "$B/$binary" "$M/bin/$binary" || return 1; else rm -f "$M/bin/$binary"; fi
 done
}
if [ "$WAS_RUNNING" -eq 1 ]; then echo Stage-stop; "$M/ctl.sh" stop || { echo Stop-failed; exit 1; }; fi
echo Stage-replace
if ! mv -f "$M/bin/tailscale.new" "$M/bin/tailscale" || ! mv -f "$M/bin/tailscaled.new" "$M/bin/tailscaled"; then
 restore || { echo Restore-failed; exit 1; }
 [ "$WAS_RUNNING" -ne 1 ] || "$M/ctl.sh" start
 exit 1
fi
if [ "$WAS_RUNNING" -eq 1 ]; then
 echo Stage-restart
 if ! "$M/ctl.sh" start; then
  echo New-daemon-start-failed
  "$M/ctl.sh" stop || { echo Stop-new-daemon-failed; exit 1; }
  restore || { echo Restore-failed; exit 1; }
  "$M/ctl.sh" start
  exit 1
 fi
fi
printf 'Tailscale %s\nSource: %s/%s\nSHA256: %s\n' "$V" "$U" "$A" "$H" > "$D/installed-provenance.txt"
echo "Update-finished-$V"
echo "Backup-$B"
echo 'Use WebUI Start and Login, then verify app access'
