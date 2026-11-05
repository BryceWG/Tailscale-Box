# Tailscale Box

This is an ARM64 Android root module. Module metadata lives in `module.prop`;
keep `version` and numeric `versionCode` in sync when releasing. Never commit
runtime identity, authentication links, logs, downloaded archives, or build ZIPs.

Root entrypoints use `/system/bin/sh` and Android shell utilities. Keep scripts
compatible with Android mksh and POSIX sh; do not assume bash, Python, jq, npm,
or GNU-only flags exist on the phone. Python build tooling runs through `uv`.

`ctl.sh` owns daemon lifecycle and destination routing. Runtime data and identity
live under `/data/adb/tailscale-direct`, outside the module directory, and survive
uninstall. Preserve accept-dns=false, accept-routes=false, netfilter-mode=off,
the native TUN, and destination-only priority 70/table 52 routing. Do not change
system DNS, SELinux, default routes, or other modules' rules.

`action.sh` invokes `update.sh`. Both an empty installation and an existing pair
of Tailscale binaries must work. Verify official SHA256 before replacing files;
keep rollback and existing state intact. Only ARM64 is supported.

`webroot/` contains the self-contained manager WebUI. `webui.sh` is its fixed
command JSON bridge. Do not expose a root HTTP listener or interpolate page data
into shell commands. Render peer and daemon text with textContent. Keep login
URLs out of diagnostics and source control. Official Magisk has no native WebUI;
KernelSU/APatch or a compatible WebUI host is required.

`scripts/build.py` packages an explicit module file list into minimal and bundled
ZIPs. Minimal packages exclude both binaries. Bundled packages resolve official
stable Tailscale at build time, verify its checksum, and record provenance.
GitHub Actions compares module versions against the push base before packaging.

Run `sh -n` for changed shell scripts, `node --check` for changed JavaScript, and
`uv run --no-project python -m unittest discover -s tests` for packaging/bridge
changes. Check ZIP contents and execute meaningful mocked boundary tests; do not
add tests which only repeat implementation. Android routing, manager bridges,
and ARM64 binaries still require device validation; report that limit plainly.

Use concise prose and plain language. Avoid unnecessary fallbacks and defensive
scaffolding. After a substantial change, request a low-effort subagent review and
fix its findings. Coordinate ownership before editing files another agent owns.
