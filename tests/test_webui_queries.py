"""Read-only bridge integration checks with fake CLI/network boundaries."""
import json
import os
from pathlib import Path
import subprocess
from http.server import BaseHTTPRequestHandler
import socketserver
import threading
from urllib.parse import parse_qs
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

    def test_ping_unix_api_paths_and_failures_without_cli_delay(self):
        observed = []
        replies = [
            (200, {'LatencySeconds': 0.015, 'Endpoint': '192.0.2.2:41641', 'PeerRelay': '', 'DERPRegionID': 0, 'Err': ''}),
            (200, {'LatencySeconds': 0.023, 'Endpoint': '', 'PeerRelay': '192.0.2.3:40000:vni:1', 'DERPRegionID': 0, 'Err': ''}),
            (200, {'LatencySeconds': 0.090, 'Endpoint': '', 'PeerRelay': '', 'DERPRegionID': 900, 'DERPRegionCode': 'aliyun-gz', 'Err': ''}),
            (200, {'LatencySeconds': 0, 'Err': 'no matching peer'}),
            (500, {'error': 'daemon failure'}),
        ]

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                observed.append((self.path, self.headers['Host'], parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())))
                status, body = replies.pop(0)
                content = json.dumps(body).encode() if status == 200 else b''
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                if content:
                    self.wfile.write(content)

            def log_message(self, *args):
                pass

        with socketserver.UnixStreamServer(str(self.path / 'data/tailscaled.sock'), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                # A bogus proxy must not affect the local Unix-socket transport.
                self.env['http_proxy'] = 'http://127.0.0.1:1'
                addresses = ['100.64.0.2', 'fd7a:115c:a1e0::2', '100.64.0.3']
                for address in addresses:
                    result = self.query('ping', address)
                    self.assertEqual(result['exitCode'], 0)
                    self.assertEqual(result['result']['Err'], '')
                    self.assertEqual(observed[-1], ('/localapi/v0/ping', 'local-tailscaled.sock', {'ip': [address], 'type': ['disco']}))
                result = self.query('ping', '100.64.0.2')
                self.assertEqual(result['result']['Err'], 'no matching peer')
                result = self.query('ping', '100.64.0.2')
                self.assertNotEqual(result['exitCode'], 0)
                self.assertIsNone(result['result'])
                self.assertIn('500', result['output'])
                count = len(observed)
                for args in ((), ('--help',), ('100.64.0.2;touch /tmp/unsafe',), ('100.64.0.2', 'extra')):
                    self.assertNotEqual(self.query('ping', *args)['exitCode'], 0)
                self.assertEqual(len(observed), count)
                self.assertFalse((self.path / 'timeout').exists())
            finally:
                server.shutdown()
                thread.join()

    def test_ping_curl_failure_is_reported_as_json(self):
        self.executable('tools/curl', 'echo "request timed out" >&2\nexit 28')
        result = self.query('ping', '100.64.0.2')
        self.assertEqual(result['exitCode'], 28)
        self.assertIsNone(result['result'])
        self.assertIn('timed out', result['output'])
