"""Read-only Android diagnosis boundary: commands, bounded probes, redaction."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NetworkDiagnose(unittest.TestCase):
    def test_ctl_defaults_to_android_interface_binding_and_allows_override(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / 'bin').mkdir()
            (p / 'ctl.sh').write_text((ROOT / 'ctl.sh').read_text().replace('/data/adb/tailscale-direct', str(p / 'data')))
            cli = p / 'bin/tailscale'
            cli.write_text('#!/bin/sh\nprintf "%s" "$TS_FORCE_LINUX_BIND_TO_DEVICE"\n')
            cli.chmod(0o755)
            env = dict(os.environ)
            env.pop('TS_FORCE_LINUX_BIND_TO_DEVICE', None)
            args = ['sh', str(p / 'ctl.sh'), 'ts', 'netcheck']
            self.assertEqual(subprocess.check_output(args, env=env, text=True), '1')
            env['TS_FORCE_LINUX_BIND_TO_DEVICE'] = '0'
            self.assertEqual(subprocess.check_output(args, env=env, text=True), '0')

    def test_bounded_probe_read_only_commands_and_auth_redaction(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / 'bin').mkdir()
            (p / 'tools').mkdir()
            (p / 'data').mkdir()
            (p / 'network-diagnose.sh').write_text((ROOT / 'network-diagnose.sh').read_text().replace('/data/adb/tailscale-direct', str(p / 'data')))
            (p / 'data/daemon.log').write_text('magicsock: network is unreachable\nrelay https://login.tailscale.com/a/private-token\n')
            def executable(path, content):
                path.write_text('#!/bin/sh\n' + content)
                path.chmod(0o755)
            executable(p / 'tools/timeout', 'echo "timeout $1" >> "$TRACE"\nshift\nexec "$@"\n')
            executable(p / 'tools/ip', 'echo "ip $*" >> "$TRACE"\necho mock-route\n')
            executable(p / 'tools/ss', 'echo "ss $*" >> "$TRACE"\n')
            executable(p / 'tools/getprop', 'echo 16\n')
            executable(p / 'bin/tailscaled', 'echo 1.104.5\n')
            executable(p / 'bin/tailscale', 'echo "tailscale $*" >> "$TRACE"\necho \'{"PeerRelay":"relay.example:40000:vni:1","AuthURL":"https://controlplane.tailscale.com/a/private-token"}\'\n')
            env = dict(os.environ, PATH=str(p / 'tools') + ':' + os.environ['PATH'], TRACE=str(p / 'trace'))
            result = subprocess.run(['sh', str(p / 'network-diagnose.sh'), '100.64.0.2'], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('private-token', result.stdout)
            self.assertIn('[login URL redacted]', result.stdout)
            self.assertIn('PeerRelay', result.stdout)
            trace = (p / 'trace').read_text()
            self.assertIn('timeout 25', trace)
            self.assertIn('ping --c 10 --timeout=2s 100.64.0.2', trace)
            self.assertIn('route get 1.1.1.1 mark 0x80000 uid 0', trace)
            self.assertNotRegex(trace, r'\b(add|del|flush|replace|restart)\b')
            (p / 'trace').unlink()
            result = subprocess.run(['sh', str(p / 'network-diagnose.sh'), '--help; touch /tmp/unsafe'], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((p / 'trace').exists())
