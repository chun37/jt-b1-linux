import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_dictionary
import inspect_device
import stage
import spotify_backend

ROOT = Path(__file__).resolve().parents[2]


class ToolsTests(unittest.TestCase):
    def test_dictionary_indexes_kana_and_never_evaluates_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'SKK-JISYO.L', Path(tmp) / 'kana.sqlite'
            source.write_text('; synthetic dictionary\nはつね /初音;name/(danger)/\nabc /ignored/\n', encoding='euc_jp')
            self.assertEqual(build_dictionary.build(source, target), 1)
            with sqlite3.connect(target) as db:
                self.assertEqual(json.loads(db.execute('SELECT candidates FROM words').fetchone()[0]), ['初音'])
            with self.assertRaises(FileExistsError):
                build_dictionary.build(source, target)

    def test_dictionary_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, link = Path(tmp) / 'source', Path(tmp) / 'link'
            source.write_text('unchanged')
            link.symlink_to(source)
            with self.assertRaises(FileExistsError):
                build_dictionary.build(source, link)
            self.assertEqual(source.read_text(), 'unchanged')

    def test_inspection_whitelist_does_not_include_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, path in inspect_device.FIELDS.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('MemTotal: 123 kB\nMemFree: 45 kB' if name == 'memory' else 'synthetic')
            self.assertEqual(inspect_device.inspect(root)['memory'], 'MemTotal: 123 kB')
            self.assertEqual(set(inspect_device.inspect(root)), set(inspect_device.FIELDS))
            self.assertFalse(any('serial' in path or 'net/' in path or 'dpo' in path for path in inspect_device.FIELDS.values()))

    def test_stage_is_new_local_tree_and_contains_runtime_imports(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(os, 'geteuid', return_value=1000):
            output = Path(tmp) / 'staging'
            self.assertGreater(stage.stage(output), 20)
            self.assertTrue((output / 'opt/jtb1-gui/app/boot_playback.py').is_file())
            self.assertTrue((output / 'usr/local/lib/jtb1-povo/modem_session.py').is_file())
            self.assertEqual(json.loads((output / 'opt/jtb1-gui/data/empty.json').read_text()), {})
            self.assertFalse((output / 'etc/inittab').exists())
            with self.assertRaises(FileExistsError):
                stage.stage(output)

    def test_stage_rejects_root(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(os, 'geteuid', return_value=0):
            output = Path(tmp) / 'staging'
            with self.assertRaises(PermissionError):
                stage.stage(output)
            self.assertFalse(output.exists())

    def test_client_id_requires_valid_reader_configuration(self):
        with patch.dict(os.environ, {'JTB1_SPOTIFY_CLIENT_ID': 'not-a-client-id'}):
            with self.assertRaises(ValueError):
                spotify_backend.configured_client_id()
        with patch.dict(os.environ, {'JTB1_SPOTIFY_CLIENT_ID': 'a' * 32}):
            self.assertEqual(spotify_backend.configured_client_id(), 'a' * 32)

    def test_missing_client_id_never_starts_network_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(spotify_backend, 'CLIENT_ID', ''):
            api = spotify_backend.WebAPI(Path(tmp))
            with self.assertRaises(spotify_backend.BackendError):
                api.token()

    def test_cellular_verifier_import_has_no_hardware_io(self):
        path = ROOT / 'cellular/verify_cellular_https.py'
        spec = importlib.util.spec_from_file_location('cellular_verifier', path)
        module = importlib.util.module_from_spec(spec)
        with patch.object(Path, 'read_text', side_effect=AssertionError('hardware access at import')):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))

    def test_cellular_verifier_requires_explicit_network_opt_in(self):
        result = subprocess.run([sys.executable, str(ROOT / 'cellular/verify_cellular_https.py')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--confirm-network-test is required', result.stderr)

    def test_offline_home_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'home.png'
            result = subprocess.run([sys.executable, str(ROOT / 'tools/render_home.py'), '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            from PIL import Image
            with Image.open(output) as image:
                self.assertEqual(image.size, (600, 1024))
