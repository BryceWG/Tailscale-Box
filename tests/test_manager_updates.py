"""Validate manager discovery and minimal upgrades from either package flavor."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts.build import ROOT, build, metadata, REPOSITORY


class ManagerUpdates(unittest.TestCase):
    def test_each_flavor_discovers_its_own_update_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            minimal = build('minimal', output)
            manifest_before = (output / 'update.json').read_bytes()
            with patch('scripts.build.latest_binaries', return_value=({'bin/tailscale': b'cli', 'bin/tailscaled': b'daemon'}, 'test provenance')):
                bundled = build('bundled', output)
            self.assertEqual((output / 'update.json').read_bytes(), manifest_before)
            manifest = json.loads(manifest_before)
            props = metadata()
            self.assertEqual(manifest['version'], props['version'])
            self.assertEqual(manifest['versionCode'], int(props['versionCode']))
            base = f'{REPOSITORY}/releases/download/v{props["version"]}/'
            self.assertEqual(manifest['zipUrl'], base + minimal.name)
            self.assertEqual(manifest['changelog'], base + 'changelog.md')
            self.assertTrue((output / 'changelog.md').is_file())
            for package in (minimal, bundled):
                with ZipFile(package) as archive:
                    prop = dict(line.split('=', 1) for line in archive.read('module.prop').decode().splitlines() if '=' in line)
                    variant = 'minimal' if package == minimal else 'bundled'
                    manifest_name = 'update.json' if variant == 'minimal' else 'update-bundled.json'
                    own_manifest = json.loads((output / manifest_name).read_text())
                    self.assertEqual(prop['updateJson'], REPOSITORY + '/releases/latest/download/' + manifest_name)
                    self.assertEqual(own_manifest['zipUrl'], base + package.name)
                    self.assertEqual(own_manifest['versionCode'], int(prop['versionCode']))
                    self.assertEqual(prop['versionCode'], str(manifest['versionCode']))
            with ZipFile(bundled) as archive:
                self.assertIn('bin/tailscale', archive.namelist())
                self.assertIn('bin/tailscaled', archive.namelist())
            with ZipFile(minimal) as archive:
                self.assertNotIn('bin/tailscale', archive.namelist())
                self.assertNotIn('bin/tailscaled', archive.namelist())

    def test_minimal_upgrade_preserves_installed_binaries_and_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old = base / 'old'
            new = base / 'new'
            data = base / 'data'
            data.mkdir()
            (data / 'tailscaled.state').write_bytes(b'private identity')
            (old / 'bin').mkdir(parents=True)
            for name in ('tailscale', 'tailscaled'):
                path = old / 'bin' / name
                path.write_bytes(b'existing binary ' + name.encode())
                path.chmod(0o755)
            package = build('minimal', base / 'dist')
            with ZipFile(package) as archive:
                archive.extractall(new)
            script = (ROOT / 'customize.sh').read_text().replace('/data/adb/tailscale-direct', str(data)).replace('/data/adb/modules/tailscale_direct', str(old)).replace('/data/adb/modules/magisk-tailscaled', str(base / 'absent-old-module'))
            installer = base / 'install.sh'
            installer.write_text('''abort() { echo "$1"; exit 1; }
ui_print() { echo "$1"; }
set_perm() { chmod "$4" "$1"; }
''' + script)
            env = dict(os.environ, BOOTMODE='true', ARCH='arm64', MODPATH=str(new))
            subprocess.run(['sh', str(installer)], env=env, check=True, capture_output=True)
            for name in ('tailscale', 'tailscaled'):
                self.assertEqual((new / 'bin' / name).read_bytes(), (old / 'bin' / name).read_bytes())
                self.assertTrue(os.access(new / 'bin' / name, os.X_OK))
            self.assertEqual((data / 'tailscaled.state').read_bytes(), b'private identity')
