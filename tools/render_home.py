#!/usr/bin/env python3
"""Render a synthetic home-screen PNG on the PC; no device or API access."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gui/app'))
import standby_screen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', type=Path, default=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'))
    parser.add_argument('--output', type=Path, required=True, help='new PNG path; no overwrite')
    args = parser.parse_args()
    image = standby_screen.render_screen(standby_screen.load_fonts(args.font),
            {'updated_at': __import__('time').time(), 'battery': 75,
             'active_interface': 'wlan0', 'wifi': {'bars': 3}}, now=0)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        image.save(output, format='PNG')
    print(args.output)


if __name__ == '__main__':
    main()
