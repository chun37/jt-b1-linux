"""Read-only system indicators. Never opens AT/DIAG or changes network settings."""
import fcntl
import json
import os
import re
import select
import struct
import subprocess
import time
from pathlib import Path


def read(path, default=''):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def parse_serving(values):
    data = values.get(1, b'')
    if len(data) < 5 or len(data) < 5 + data[4]:
        raise ValueError('invalid serving system')
    registered = data[0] == 1
    interfaces = data[5:5 + data[4]]
    names = {1: 'CDMA', 2: 'EV-DO', 4: '2G', 5: '3G', 8: 'LTE', 9: 'TD-SCDMA'}
    rat = next((names[k] for k in (8, 5, 9, 2, 1, 4) if k in interfaces), '不明')
    return {'registered': registered, 'rat': rat if registered else '圏外'}


def nas_status(path='/dev/qcqmi0'):
    # GobiNet allocates a separate NAS client for this fd. Only Get Serving
    # System (0x24) is sent. No WDS handles or serial ports are shared.
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        fcntl.ioctl(fd, 0x8be1, 3)
        request = struct.pack('<BHHH', 0, 1, 0x24, 0)
        if os.write(fd, request) != len(request):
            raise OSError('short NAS request')
        end = time.monotonic() + 3
        while time.monotonic() < end:
            if not select.select([fd], [], [], .2)[0]:
                continue
            data = os.read(fd, 16384)
            if len(data) < 7 or data[0] != 2 or struct.unpack_from('<HH', data, 1) != (1, 0x24):
                continue
            if len(data) != 7 + struct.unpack_from('<H', data, 5)[0]:
                raise ValueError('invalid NAS SDU')
            values, offset = {}, 7
            while offset < len(data):
                if offset + 3 > len(data):
                    raise ValueError('invalid NAS TLV')
                kind, size = struct.unpack_from('<BH', data, offset)
                offset += 3
                if offset + size > len(data):
                    raise ValueError('invalid NAS value')
                values[kind] = data[offset:offset + size]
                offset += size
            if values.get(2) != bytes(4):
                raise ValueError('NAS query rejected')
            return parse_serving(values)
        raise TimeoutError('NAS query timed out')
    finally:
        os.close(fd)


def wifi_status():
    result = subprocess.run(['/sbin/wpa_cli', '-i', 'wlan0', 'status'],
                            capture_output=True, text=True, timeout=2)
    # Never persist SSID, BSSID, IP addresses or command output.
    connected = result.returncode == 0 and 'wpa_state=COMPLETED' in result.stdout
    match = re.search(r'^\s*wlan0:\s+\S+\s+\S+\s+(-?\d+)', read('/proc/net/wireless'), re.M)
    dbm = int(match[1]) if match else None
    bars = 0 if not connected else 1 if dbm is None else sum(dbm >= x for x in (-90, -80, -70, -60))
    return {'connected': connected, 'bars': max(1, bars) if connected else 0}


def active_interface():
    # Route lookup only: ip route get does not transmit a packet.
    result = subprocess.run(['/sbin/ip', 'route', 'get', '1.1.1.1'],
                            capture_output=True, text=True, timeout=2)
    if result.returncode:
        return ''
    match = re.search(r'\bdev\s+(\S+)', result.stdout)
    return match[1] if match else None


def network_label(state, stale=False):
    if stale:
        return '回線不明'
    interface = state.get('active_interface')
    if interface == 'wlan0':
        return 'Wi-Fi'
    if interface == 'usb0':
        rat = state.get('cell', {}).get('rat', '不明')
        return rat if rat not in ('不明', '圏外') else '携帯'
    return 'オフライン' if interface == '' else '回線不明'


def collect():
    try:
        wifi = wifi_status()
    except (OSError, subprocess.SubprocessError):
        wifi = {'connected': None, 'bars': 0}
    try:
        cell = nas_status()
    except (OSError, ValueError, TimeoutError):
        cell = {'registered': None, 'rat': '不明'}
    try:
        state = json.loads(read('/run/jtb1-povo/state.json', '{}'))
        data_up = state.get('status') == 'connected' and 0 <= time.time() - state['updated_at'] < 45
    except (ValueError, KeyError, TypeError):
        data_up = False
    cell['data_connected'] = data_up
    try:
        capacity = int(read('/sys/class/power_supply/battery/capacity'))
        if not 0 <= capacity <= 100:
            capacity = None
    except ValueError:
        capacity = None
    try:
        interface = active_interface()
    except (OSError, subprocess.SubprocessError):
        interface = None
    return {'wifi': wifi, 'cell': cell, 'battery': capacity, 'active_interface': interface,
            'charging': read('/sys/class/power_supply/smb137c/status') in ('Charging', 'Full'),
            'updated_at': time.time()}


def render_bar(fonts, state, now=None):
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (600, 44), '#080e14')
    draw = ImageDraw.Draw(image)
    white, dim, green = '#f4f6f8', '#8796a6', '#56dda2'
    clock = time.strftime('%H:%M', time.gmtime((time.time() if now is None else now) + 9 * 3600))
    draw.text((16, 5), clock, font=fonts[22], fill=white)
    stale = time.time() - state.get('updated_at', 0) > 30
    wifi = state.get('wifi', {})
    label = network_label(state, stale)
    draw.text((286, 7), label, font=fonts[18], fill=white if not stale else dim)
    if label == 'Wi-Fi':
        for i in range(4):
            height = 6 + i * 5
            draw.rectangle((369 + i * 7, 31 - height, 373 + i * 7, 31), fill=green if i < wifi.get('bars', 0) else '#303c48')
    capacity = state.get('battery') if not stale else None
    percentage = str(capacity) + '%' if capacity is not None else '--%'
    text_x = 584 - round(fonts[18].getlength(percentage))
    icon_x = text_x - 52
    draw.rounded_rectangle((icon_x, 12, icon_x + 34, 30), radius=3, outline=dim, width=2)
    draw.rectangle((icon_x + 35, 18, icon_x + 38, 24), fill=dim)
    if capacity:
        draw.rectangle((icon_x + 3, 15, icon_x + 3 + round(28 * capacity / 100), 27), fill=green if capacity > 15 else '#ff9e83')
    draw.text((text_x, 7), percentage, font=fonts[18], fill=white)
    draw.line((0, 43, 599, 43), fill='#263544')
    return image
