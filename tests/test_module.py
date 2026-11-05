"""Boundary tests for distribution contents and the Android updater transaction."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build', ROOT / 'scripts/build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class Packaging(unittest.TestCase):
    def test_minimal_has_webui_and_no_binaries_or_private_files(self):
        with tempfile.TemporaryDirectory() as folder:
            target = build.build('minimal', Path(folder))
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                self.assertIn('webroot/index.html', names)
                self.assertIn('webui.sh', names)
                self.assertFalse(any(n.startswith(('bin/', '.git/', 'tests/')) for n in names))
                self.assertEqual(archive.getinfo('ctl.sh').external_attr >> 16 & 0o777, 0o755)

    def test_official_checksum_is_mandatory(self):
        responses = [b'{"Tarballs":{"arm64":"tailscale_1.2.3_arm64.tgz"}}', b'corrupt', b'0' * 64]
        with patch.object(build, 'fetch', side_effect=responses):
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                build.latest_binaries()

    def test_bundled_records_exact_download_provenance(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
            for name in ('tailscale', 'tailscaled'):
                content = name.encode()
                info = tarfile.TarInfo('tailscale_1.2.3_arm64/' + name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        archive = buffer.getvalue()
        digest = hashlib.sha256(archive).hexdigest()
        with patch.object(build, 'fetch', side_effect=[b'{"Tarballs":{"arm64":"tailscale_1.2.3_arm64.tgz"}}', archive, digest.encode()]):
            binaries, provenance = build.latest_binaries()
        self.assertEqual(binaries['bin/tailscaled'], b'tailscaled')
        self.assertIn(digest, provenance)


class Updater(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.module = self.base / 'module'
        self.module.mkdir()
        self.data = self.base / 'data'
        self.data.mkdir()
        self.tools = self.base / 'tools'
        self.tools.mkdir()
        script = (ROOT / 'update.sh').read_text().replace('/data/adb/tailscale-direct', str(self.data))
        (self.module / 'update.sh').write_text(script)
        self.write_exec(self.module / 'ctl.sh', 'exit 0')
        self.write_exec(self.tools / 'curl', '''while [ "$#" -gt 0 ]; do
case "$1" in -o) shift; out=$1;; https://*) url=$1;; esac; shift; done
case "$url" in *mode=json*) cp "$FIXTURE/meta" "$out";; *.sha256) cp "$FIXTURE/checksum" "$out";; *) cp "$FIXTURE/archive" "$out";; esac''')
        self.write_exec(self.tools / 'sha256sum', 'shasum -a 256 "$@"')
        self.env = dict(os.environ, PATH=str(self.tools) + ':' + os.environ['PATH'], FIXTURE=str(self.base))
        (self.base / 'meta').write_text('{"Tarballs":{"arm64":"tailscale_1.2.3_arm64.tgz"}}')
        with tarfile.open(self.base / 'archive', 'w:gz') as tar:
            for name in ('tailscale', 'tailscaled'):
                content = b'#!/bin/sh\necho 1.2.3\n'
                info = tarfile.TarInfo('tailscale_1.2.3_arm64/' + name)
                info.mode = 0o755
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        digest = hashlib.sha256((self.base / 'archive').read_bytes()).hexdigest()
        (self.base / 'checksum').write_text(digest)

    def write_exec(self, path, content):
        path.write_text('#!/bin/sh\n' + content + '\n')
        path.chmod(0o755)

    def run_update(self):
        return subprocess.run(['sh', str(self.module / 'update.sh')], env=self.env, capture_output=True, text=True)

    def test_first_install_and_repeated_action(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.module / 'bin/tailscaled').exists())
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Already-current', result.stdout)
        self.assertFalse((self.data / 'update.lock').exists())

    def test_bad_checksum_preserves_existing_binary_and_identity(self):
        (self.module / 'bin').mkdir()
        for name in ('tailscale', 'tailscaled'):
            self.write_exec(self.module / 'bin' / name, 'echo 1.0.0')
        old = (self.module / 'bin/tailscale').read_bytes()
        (self.data / 'tailscaled.state').write_text('private identity')
        (self.base / 'checksum').write_text('0' * 64)
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checksum-mismatch', result.stdout)
        self.assertEqual((self.module / 'bin/tailscale').read_bytes(), old)
        self.assertEqual((self.data / 'tailscaled.state').read_text(), 'private identity')
        self.assertFalse((self.data / 'update.lock').exists())

    def test_second_move_failure_rolls_back_both_binaries(self):
        (self.module / 'bin').mkdir()
        for name in ('tailscale', 'tailscaled'):
            self.write_exec(self.module / 'bin' / name, 'echo 1.0.0')
        before = [(self.module / 'bin' / name).read_bytes() for name in ('tailscale', 'tailscaled')]
        real_mv = shutil.which('mv')
        self.write_exec(self.tools / 'mv', f'case "$*" in *tailscaled.new*) exit 1;; esac\nexec "{real_mv}" "$@"')
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        after = [(self.module / 'bin' / name).read_bytes() for name in ('tailscale', 'tailscaled')]
        self.assertEqual(after, before)


class Bridge(unittest.TestCase):
    def test_missing_installation_and_json_escaping(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            bridge = (ROOT / 'webui.sh').read_text().replace('/data/adb/tailscale-direct', str(path / 'data'))
            (path / 'webui.sh').write_text(bridge)
            result = subprocess.check_output(['sh', str(path / 'webui.sh'), 'status'], text=True)
            self.assertEqual(json.loads(result)['installed'], False)
            (path / 'bin').mkdir()
            for name in ('tailscale', 'tailscaled'):
                (path / 'bin' / name).touch(mode=0o755)
            (path / 'ctl.sh').write_text('#!/bin/sh\nprintf \'a"b\\tc\\r\\nnext\\n\'\nexit 7\n')
            (path / 'ctl.sh').chmod(0o755)
            result = subprocess.check_output(['sh', str(path / 'webui.sh'), 'repair'], text=True)
            value = json.loads(result)
            self.assertEqual(value['exitCode'], 7)
            self.assertEqual(value['output'], 'a"b\tc\r\nnext')


class ManagerAction(unittest.TestCase):
    def test_pipeline_preserves_failure_with_stale_update_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            data = path / 'data'
            data.mkdir()
            (data / 'update.exit').write_text('0')
            action = (ROOT / 'action.sh').read_text().replace('/data/adb/tailscale-direct', str(data)).replace('/system/bin/sh', '/bin/sh')
            (path / 'action.sh').write_text(action)
            (path / 'update.sh').write_text('echo "Updater busy"\nexit 3\n')
            result = subprocess.run(['sh', str(path / 'action.sh')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 3)
            self.assertIn('Updater busy', result.stdout)
