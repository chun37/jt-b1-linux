"""Short-release physical controls, owned by the existing GUI supervisor.
No evdev grab, kernel power policy, volume change, or repeat action.
"""
import json
import os
import select
import struct
import subprocess
import sys
import time
from pathlib import Path
from auth_helper import atomic_json
from spotify_backend import Player, ctl, start_audio, cancel_orphan_controls

EVENT = struct.Struct('llHHi')
MAPPING = {'omap4-keypad': {158: 'prev', 102: 'toggle', 139: 'next'},
           'twl4030_pwrbutton': {116: 'home'}}
PY = '/opt/jtb1-gui/bin/jtb1-gui-python'
RUN = Path('/run/jtb1-gui')


class ShortPress:
    def __init__(self, mapping):
        self.mapping = mapping
        self.down = {}
        self.dropped = False
        self.last = {}

    def feed(self, kind, code, value, now):
        if kind == 0 and code == 3:  # SYN_DROPPED: never guess missing releases
            self.down.clear()
            self.dropped = True
        if self.dropped:
            if kind == 0 and code == 0:
                self.dropped = False
            return None
        if kind != 1 or code not in self.mapping:
            return None
        if value == 1:
            if code not in self.down:
                # Chords invalidate every involved press.
                chord = bool(self.down)
                if chord:
                    self.down = {k: (t, False) for k, (t, valid) in self.down.items()}
                self.down[code] = (now, not chord)
        elif value == 0:
            press = self.down.pop(code, None)
            if press:
                started, valid = press
                if valid and 0 <= now - started < .8 and now - self.last.get(code, -100) >= .15:
                    self.last[code] = now
                    return self.mapping[code]
        return None


def perform(action, player=None, caller=ctl):
    player = player or Player(starter=start_audio)
    if action == 'toggle':
        status = caller('now').get('status')
        if status == 'playing':
            action = 'pause'
        elif status in ('paused', 'stopped'):
            action = 'play'
        else:
            raise ValueError('unknown playback state')
    if action not in ('prev', 'next', 'play', 'pause'):
        raise ValueError('unsupported physical action')
    player.action(action)


class Buttons:
    def __init__(self):
        self.inputs = {}
        self.worker = None
        self.started = 0
        self.count = 0
        self.last_scan = -100
        self.scan()

    def status(self, **extra):
        atomic_json(RUN / 'buttons.json', dict(manager_pid=os.getpid(),
                    devices=sorted(v[0] for v in self.inputs.values()),
                    accepted=self.count, **extra))

    def scan(self):
        now = time.monotonic()
        if now - self.last_scan < 3:
            return
        self.last_scan = now
        names = {v[0] for v in self.inputs.values()}
        for path in Path('/sys/class/input').glob('event*'):
            try:
                name = (path / 'device/name').read_text().strip()
                if name not in MAPPING or name in names:
                    continue
                fd = os.open('/dev/input/' + path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                self.inputs[fd] = (name, ShortPress(MAPPING[name]))
                names.add(name)
            except OSError:
                continue
        self.status()

    def reap(self, stop=False):
        process = self.worker
        if process is None:
            return
        if stop or time.monotonic() - self.started > 25:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        if process.poll() is not None:
            cancel_orphan_controls(process.pid)
            self.worker = None
            self.status(result='ok' if process.returncode == 0 else 'failed')

    def pump(self):
        self.reap()
        self.scan()
        home = False
        if not self.inputs:
            return home
        ready, _, _ = select.select(list(self.inputs), [], [], 0)
        for fd in ready:
            name, decoder = self.inputs[fd]
            try:
                raw = os.read(fd, EVENT.size * 64)
                if not raw or len(raw) % EVENT.size:
                    raise OSError('incomplete button event')
            except OSError:
                os.close(fd)
                del self.inputs[fd]
                continue
            for sec, usec, kind, code, value in EVENT.iter_unpack(raw):
                action = decoder.feed(kind, code, value, sec + usec / 1000000)
                if action == 'home':
                    home = True
                    self.count += 1
                    self.status(last_action='home')
                elif action and self.worker is None:
                    self.worker = subprocess.Popen([PY, str(Path(__file__).resolve()), action],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, start_new_session=True)
                    self.started = time.monotonic()
                    self.count += 1
                    self.status(last_action=action)
        return home

    def close(self):
        self.reap(stop=True)
        for fd in self.inputs:
            os.close(fd)
        self.inputs.clear()


if __name__ == '__main__':
    try:
        perform(sys.argv[1])
    except Exception:
        raise SystemExit(1)
