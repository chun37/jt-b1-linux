#!/usr/bin/env python3
"""Print selected OS/display/battery values; never emit serials, network IDs or secrets."""
import argparse
import json
from pathlib import Path

FIELDS = {
    'kernel': 'proc/sys/kernel/osrelease',
    'pid1': 'proc/1/comm',
    'memory': 'proc/meminfo',
    'display_size': 'sys/class/graphics/fb0/virtual_size',
    'battery_capacity': 'sys/class/power_supply/battery/capacity',
    'battery_status': 'sys/class/power_supply/smb137c/status',
}


def inspect(root):
    result = {}
    for name, relative in FIELDS.items():
        try:
            value = (root / relative).read_text()[:4096].strip()
            if name == 'memory':
                value = next((line for line in value.splitlines() if line.startswith('MemTotal:')), 'unknown')
            result[name] = value
        except OSError:
            result[name] = None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'), help='filesystem root (also useful for synthetic fixtures)')
    args = parser.parse_args()
    print(json.dumps(inspect(args.root), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
