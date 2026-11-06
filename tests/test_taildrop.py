"""Taildrop receiver lifecycle with real subprocesses and a fake CLI boundary."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Taildrop(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = p = Path(self.temp.name)
        for name in ('data', 'bin', 'tools', 'proc', 'storage'):
            (p / name).mkdir()
        self.dest = p / 'storage/Download/taildrop'
        script = (ROOT / 'taildrop.sh').read_text().replace('/data/adb/tailscale-direct', str(p / 'data')).replace('/storage/emulated/0', str(p / 'storage')).replace('/system/bin/sh', '/bin/sh').replace('/proc/', str(p / 'proc') + '/')
        # The test's daemon socket is a fixture marker; no real daemon is required.
        script = script.replace('[ -S "$SOCK" ]', '[ -f "$SOCK" ]')
        (p / 'data/tailscaled.sock').touch()
        (p / 'taildrop.sh').write_text(script)
        self.env = dict(os.environ, PATH=str(p / 'tools') + ':' + os.environ['PATH'], FIXTURE=str(p))
        self.write_exec('tools/nohup', '''mkdir -p "$FIXTURE/proc/$$"
printf '%s\\0' "$@" > "$FIXTURE/proc/$$/cmdline"
exec "$@"''')
        self.write_exec('bin/tailscale', '''mkdir -p "$FIXTURE/proc/$$"
printf '%s\\0' "$0" "$@" > "$FIXTURE/proc/$$/cmdline"
printf '%s\\n' "$@" > "$FIXTURE/args"
umask > "$FIXTURE/mask"
echo $$ >> "$FIXTURE/invocations"
trap 'rm -f "$FIXTURE/proc/$$/cmdline"; exit 0' TERM INT
while :; do sleep 0.1; done''')
        self.addCleanup(self.cleanup_worker)

    def write_exec(self, name, text):
        file = self.p / name
        file.write_text('#!/bin/sh\n' + text + '\n')
        file.chmod(0o755)

    def run_control(self, command):
        return subprocess.run(['sh', str(self.p / 'taildrop.sh'), command], env=self.env, capture_output=True, text=True, timeout=15)

    def cleanup_worker(self):
        try:
            self.run_control('stop')
        except (OSError, subprocess.TimeoutExpired):
            pass

    def test_start_is_idempotent_stop_preserves_files_and_checks_cli_flags(self):
        result = self.run_control('start')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = (self.p / 'args').read_text().splitlines()
        self.assertEqual(args, [f'--socket={self.p}/data/tailscaled.sock', 'file', 'get', '--loop', '--conflict=rename', str(self.dest)])
        self.assertEqual(int((self.p / 'mask').read_text().strip(), 8), 0o22)
        self.assertTrue(self.dest.is_dir())
        result = self.run_control('start')
        self.assertEqual(result.returncode, 0)
        self.assertIn('already running', result.stdout)
        self.assertEqual(len((self.p / 'invocations').read_text().splitlines()), 1)
        (self.dest / 'keep.txt').write_text('downloaded file')
        result = self.run_control('stop')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.p / 'data/taildrop.pid').exists())
        self.assertEqual((self.dest / 'keep.txt').read_text(), 'downloaded file')

    def test_stale_pid_does_not_signal_unrelated_process(self):
        unrelated = subprocess.Popen(['sleep', '30'])
        self.addCleanup(unrelated.wait)
        self.addCleanup(unrelated.terminate)
        (self.p / f'proc/{unrelated.pid}').mkdir()
        (self.p / f'proc/{unrelated.pid}/cmdline').write_bytes(b'sleep\x0030\x00')
        (self.p / 'data/taildrop.pid').write_text(str(unrelated.pid))
        result = self.run_control('stop')
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(unrelated.poll())

    def test_receiver_failure_does_not_claim_running(self):
        self.write_exec('bin/tailscale', "echo 'unwritable storage' >&2\nexit 1")
        result = self.run_control('start')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exited', result.stdout)
        self.assertIn('unwritable storage', (self.p / 'data/taildrop.log').read_text())
        self.assertFalse((self.p / 'data/taildrop.pid').exists())

    def test_missing_socket_starts_no_receiver(self):
        (self.p / 'data/tailscaled.sock').unlink()
        self.assertNotEqual(self.run_control('start').returncode, 0)
        self.assertFalse((self.p / 'invocations').exists())

    def test_locked_storage_does_not_create_unmounted_destination(self):
        (self.p / 'storage').rmdir()
        self.write_exec('tools/sleep', 'exit 0')
        result = self.run_control('receive')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('destination unavailable', result.stdout)
        self.assertFalse(self.dest.exists())
        self.assertFalse((self.p / 'invocations').exists())

    def test_target_creation_failure_retries_without_manual_directory(self):
        real_mkdir = '/bin/mkdir'
        self.write_exec('tools/mkdir', f'''if [ "$2" = "$FIXTURE/storage/Download/taildrop" ] && [ ! -f "$FIXTURE/retried" ]; then
 touch "$FIXTURE/retried"
 echo 'storage still mounting' >&2
 exit 1
fi
exec {real_mkdir} "$@"''')
        self.write_exec('tools/sleep', 'exit 0')
        self.write_exec('bin/tailscale', 'printf "%s\\n" "$@" > "$FIXTURE/args"\numask > "$FIXTURE/mask"')
        result = self.run_control('receive')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.p / 'retried').exists())
        self.assertTrue(self.dest.is_dir())
        self.assertIn(str(self.dest), (self.p / 'args').read_text())
        self.assertEqual(int((self.p / 'mask').read_text().strip(), 8), 0o22)

    def test_prepare_creates_directory_and_preserves_existing_contents(self):
        self.assertEqual(self.run_control('prepare').returncode, 0)
        (self.dest / 'keep.txt').write_text('keep')
        self.assertEqual(self.run_control('prepare').returncode, 0)
        self.assertEqual((self.dest / 'keep.txt').read_text(), 'keep')
