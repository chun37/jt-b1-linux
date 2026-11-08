"""Offline tests only: never open devices or run ADB."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / 'research'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frames = load('diag_frames', RESEARCH / 'mc7700-unlock/diag_frames.py')
root = load('rootrun', RESEARCH / 'root-dmmd/rootrun.py')


class ResearchTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(frames.crc16(b'123456789'), 0x906e)

    def test_frame_roundtrip(self):
        for data in (b'abc', bytes(range(256)), b'\x7d\x7e'):
            wire = frames.frame(data)
            self.assertEqual(frames.unframe(wire[1:-1]), data)

    def test_bad_crc(self):
        self.assertIsNone(frames.unframe(b'abc\x00\x00'))
        self.assertIsNone(frames.unframe(b'\x7d'))

    def test_gsdi_packets(self):
        # Extract ONLY the pure builder, not the hardware script body.
        tree = ast.parse((RESEARCH / 'mc7700-unlock/probe_gsdi_read.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'packet')
        import struct
        scope = {'struct': struct}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<pure builder>', 'exec'), scope)
        self.assertEqual(scope['packet'](0x42).hex(), '802148000000000042000000010000000000000000000000000000000000')
        self.assertEqual(len(scope['packet'](0x44)), 34)
        with self.assertRaises(AssertionError):
            scope['packet'](0x43)

    def test_root_payload_limits(self):
        self.assertTrue(root.payload('id').startswith(root.PREFIX))
        for command in ('', ' ', 'id\0', 'a' * 241):
            with self.assertRaises(ValueError):
                root.payload(command)
        self.assertEqual(len(root.payload('a' * (240 - len(root.PREFIX)))), 240)

    def test_root_preflight_parsers(self):
        self.assertTrue(root.isolated('lo UP\nwlan0 DOWN\n'))
        self.assertFalse(root.isolated('lo UP\nwlan0 UP\n'))
        self.assertFalse(root.isolated(''))
        self.assertTrue(root.inactive('stopped\n0\n\n'))
        self.assertFalse(root.inactive('running\n1\n55818\n'))

    def test_no_flag_no_hardware(self):
        for name in ('probe_gsdi_read.py', 'unlock_network_once.py'):
            result = subprocess.run([sys.executable, str(RESEARCH / 'mc7700-unlock' / name)], capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'No device access', result.stderr)

    def test_optimized_execution_rejected(self):
        for name in ('probe_gsdi_read.py', 'unlock_network_once.py'):
            result = subprocess.run([sys.executable, '-O', str(RESEARCH / 'mc7700-unlock' / name), '--execute-on-owned-device'], capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Do not use python -O', result.stderr)


if __name__ == '__main__':
    unittest.main()
