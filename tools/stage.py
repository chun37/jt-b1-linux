#!/usr/bin/env python3
"""Assemble source files under a NEW local directory; never install on a device."""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    'gui/bin/jtb1-gui': 'usr/local/sbin/jtb1-gui',
    'gui/bin/jtb1-gui-python': 'opt/jtb1-gui/bin/jtb1-gui-python',
    'gui/bin/cli-gate': 'opt/jtb1-gui/bin/cli-gate',
    'audio/jtb1-soloist': 'usr/local/sbin/jtb1-soloist',
    'audio/jtb1-audio-watch': 'usr/local/libexec/jtb1-audio-watch',
    'display/jtb1-battery-led': 'usr/local/sbin/jtb1-battery-led',
    'wifi/jtb1-wifi': 'usr/local/sbin/jtb1-wifi',
    'wifi/jtb1-regulatory': 'usr/local/sbin/jtb1-regulatory',
    'cellular/povo_service.py': 'usr/local/libexec/jtb1-povo-service.py',
    'cellular/gobinet_wds.py': 'usr/local/lib/jtb1-povo/gobinet_wds.py',
    'cellular/modem_session.py': 'usr/local/lib/jtb1-povo/modem_session.py',
}


def stage(destination):
    destination = Path(destination)
    # Refuse privileged execution and all existing destinations, including links.
    if __import__('os').geteuid() == 0:
        raise PermissionError('run as a normal user; this is not an installer')
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    mapping = dict(FILES)
    mapping.update({str(p.relative_to(ROOT)): 'opt/jtb1-gui/app/' + p.name
                    for p in (ROOT / 'gui/app').glob('*.py')})
    for source, relative in mapping.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
        target.chmod(0o644 if relative.endswith('.py') and '/app/' in relative else 0o755)
    data = destination / 'opt/jtb1-gui/data'
    data.mkdir(parents=True)
    (data / 'empty.json').write_text('{}\n')
    (destination / 'STAGING-NOT-INSTALLED.txt').write_text(
        'Incomplete staging tree, not a flashable image or complete rootfs.\n'
        'Read docs/device.md before copying any files to a JT-B1.\n'
        'Missing: vendor modules/firmware, Python/Pillow and audio runtimes, fonts, credentials.\n')
    return len(mapping)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new local directory; existing paths refused')
    args = parser.parse_args()
    print('Staged source files:', stage(args.output))
    print('No device access or boot configuration changes were performed.')


if __name__ == '__main__':
    main()
