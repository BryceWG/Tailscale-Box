"""Read-only bridge integration checks with fake CLI/network boundaries."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Queries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = p = Path(self.temp.name)
        (p / 'data').mkdir()
        (p / 'data/webui.lock').mkdir()
        (p / 'webui.sh').write_text((ROOT / 'webui.sh').read_text().replace('/data/adb/tailscale-direct', str(p / 'data')))
        (p / 'tools').mkdir()
        self.executable('tools/timeout', 'printf "%s\\n" "$1" > "$FIXTURE/timeout"\nshift\nexec "$@"')
        (p / 'bin').mkdir()
        for name in ('tailscale', 'tailscaled'):
            self.executable('bin/' + name, 'cat "$FIXTURE/version"')
        (p / 'version').write_text('1.9.0\n')
        self.env = dict(os.environ, PATH=str(p / 'tools') + ':' + os.environ['PATH'], FIXTURE=str(p))

    def executable(self, name, content):
        path = self.path / name
        path.write_text('#!/bin/sh\n' + content + '\n')
        path.chmod(0o755)

    def query(self, *args):
        output = subprocess.check_output(['sh', str(self.path / 'webui.sh'), *args], env=self.env, text=True)
        self.assertTrue((self.path / 'data/webui.lock').is_dir())
        return json.loads(output)

    def test_check_update_versions_empty_install_and_metadata_failures(self):
        self.executable('tools/curl', '''printf '%s' "$HTTPS_PROXY" > "$FIXTURE/proxy-used"
while [ "$#" -gt 0 ]; do
 if [ "$1" = -o ]; then shift; cp "$FIXTURE/meta" "$1"; fi
 shift
done
exit "${CURL_RC:-0}"''')
        p = self.path
        (p / 'meta').write_text('{"Tarballs":{"arm64":"tailscale_1.10.0_arm64.tgz"}}')
        (p / 'data/control-proxy').write_text('http://127.0.0.1:7890')
        result = self.query('check-update')
        self.assertEqual((result['current'], result['latest'], result['available'], result['exitCode']), ('1.9.0', '1.10.0', True, 0))
        self.assertEqual((p / 'proxy-used').read_text(), 'http://127.0.0.1:7890')
        for current in ('1.10.0', '1.11.0'):
            (p / 'version').write_text(current)
            self.assertFalse(self.query('check-update')['available'])
        (p / 'bin/tailscale').unlink()
        result = self.query('check-update')
        self.assertEqual(result['current'], '')
        self.assertTrue(result['available'])
        (p / 'meta').write_text('{"error":"no ARM64 archive"}')
        result = self.query('check-update')
        self.assertNotEqual(result['exitCode'], 0)
        self.assertEqual(result['latest'], '')
        self.env['CURL_RC'] = '28'
        self.assertEqual(self.query('check-update')['exitCode'], 28)

    def test_ping_bounded_unlocked_and_cli_failure_preserved(self):
        self.executable('ctl.sh', '''printf '%s\\n' "$@" > "$FIXTURE/args"
echo 'pong from peer (100.64.0.2) via DERP(fra) in 15ms'
exit "${PING_RC:-0}"''')
        for address in ('100.64.0.2', 'fd7a:115c:a1e0::2'):
            result = self.query('ping', address)
            self.assertEqual(result['exitCode'], 0)
            self.assertIn('in 15ms', result['output'])
            self.assertEqual((self.path / 'args').read_text().splitlines(), ['ts', 'ping', '--c', '1', '--timeout=3s', '--until-direct=false', address])
            self.assertEqual((self.path / 'timeout').read_text(), '6\n')
        self.env['PING_RC'] = '124'
        self.assertEqual(self.query('ping', '100.64.0.2')['exitCode'], 124)
        (self.path / 'args').unlink()
        for args in ((), ('--help',), ('100.64.0.2;touch /tmp/unsafe',), ('100.64.0.2', 'extra')):
            self.assertNotEqual(self.query('ping', *args)['exitCode'], 0)
        self.assertFalse((self.path / 'args').exists())
