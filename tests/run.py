#!/usr/bin/env python3
"""Run PC-only tests; no ADB, physical device, or real Spotify account needed."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Synthetic, nonfunctional public-client fixture, never an actual account key.
os.environ['JTB1_SPOTIFY_CLIENT_ID'] = '0' * 32
for name in ('gui/app', 'cellular', 'tools'):
    sys.path.insert(0, str(ROOT / name))


def main():
    suite = unittest.TestSuite()
    for group in ('gui', 'cellular', 'tools'):
        directory = ROOT / 'tests' / group
        if directory.exists():
            suite.addTests(unittest.defaultTestLoader.discover(str(directory), top_level_dir=str(ROOT / 'tests')))
    return not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


if __name__ == '__main__':
    raise SystemExit(main())
