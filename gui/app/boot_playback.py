"""One autoplay attempt per OS boot, with a small persistent last-track record.
Never raises volume or loops Play after a pause/unplug. No credentials in logs.
"""
import fcntl
import json
import os
import signal
import time
from pathlib import Path
from auth_helper import atomic_json, private_directory
from spotify_backend import (BackendError, Player, WebAPI, URI, ctl, start_audio,
                             process_start, STATE, load_volume, save_volume)

RUN = Path('/run/jtb1-gui')


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def track_record(now):
    uri = (now.get('item') or {}).get('uri', '')
    match = URI.fullmatch(uri) if isinstance(uri, str) else None
    if not match or match[1] not in ('track', 'episode'):
        return None
    return {'uri': uri}


def remember(now, path):
    record = track_record(now)
    if record and read_json(path) != record:
        atomic_json(path, record)


class Autoplay:
    def __init__(self, run=RUN, state=STATE, boot_id=None, caller=ctl,
                 starter=start_audio, player=None, network=None, clock=time.monotonic,
                 cancelled=lambda: False):
        self.run, self.state = Path(run), Path(state)
        self.boot_id = boot_id or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.caller, self.starter = caller, starter
        self.player = player or Player(starter=starter)
        self.network = network or (lambda: WebAPI(state=self.state).get('/me'))
        self.clock = clock
        self.cancelled = cancelled
        self.deadline = clock() + 180
        self.marker = self.run / 'boot-playback.json'
        self.saved = self.state / 'last-track.json'
        self.preferences = self.state / 'audio-preferences.json'
        self.finished = read_json(self.marker).get('boot_id') == self.boot_id

    def mark(self, status):
        atomic_json(self.marker, {'boot_id': self.boot_id, 'status': status})

    def tick(self):
        if self.finished:
            return
        if self.clock() >= self.deadline:
            self.mark('readiness-timeout')
            self.finished = True
            return
        # All readiness failures are safe to retry: no playback request yet.
        try:
            self.network()
            self.starter()
            self.player.stable_route()
            now = self.caller('now')
        except (BackendError, OSError, ValueError):
            return
        record = track_record(now) or track_record({'item': read_json(self.saved)})
        if not record:
            self.mark('no-saved-track')
            self.finished = True
            return
        if self.cancelled():
            return
        # Durable-in-/run claim BEFORE Play, so manager/worker restart never
        # resumes a user pause or an unplug. An ambiguous failure is not retried.
        self.mark('claimed')
        self.finished = True
        if now.get('status') == 'playing':
            self.mark('already-playing')
            return
        try:
            volume = load_volume(self.preferences)
            if volume is not None:
                self.player.action('volume', volume)
            if self.cancelled():
                self.mark('cancelled-before-play')
                return
            self.player.action('play', None if track_record(now) else record['uri'])
        except (BackendError, OSError, ValueError):
            self.mark('play-failed')
            return
        self.mark('play-requested')


def main():
    os.umask(0o077)
    private_directory(RUN)
    private_directory(STATE)
    lock = os.open(RUN/'boot-playback.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        return
    parent = os.getppid()
    parent_start = process_start(parent)
    stopping = [False]
    def stop(signum, frame):
        stopping[0] = True
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, stop)
    def cancelled():
        try:
            return stopping[0] or os.getppid() != parent or process_start(parent) != parent_start
        except OSError:
            return True
    autoplay = Autoplay(cancelled=cancelled)
    next_tick = next_save = 0
    try:
        while not stopping[0]:
            try:
                if os.getppid() != parent or process_start(parent) != parent_start:
                    break
            except OSError:
                break
            now = time.monotonic()
            if now >= next_tick and not autoplay.finished:
                autoplay.tick()
                next_tick = time.monotonic() + 5
            if stopping[0]:
                break
            if now >= next_save and autoplay.finished:
                try:
                    # Serialize observations with GUI volume writers. Never save
                    # the cold-start default while waiting to restore preferences.
                    with autoplay.player.volume_guard():
                        autoplay.player.stable_route()
                        snapshot = ctl('now')
                        remember(snapshot, autoplay.saved)
                        volume = snapshot.get('volume')
                        if type(volume) is int and 0 <= volume <= 100:
                            save_volume(volume, autoplay.preferences)
                    marker = read_json(autoplay.marker)
                    if marker.get('status') == 'play-requested' and snapshot.get('status') == 'playing':
                        autoplay.mark('playing-observed')
                except (BackendError, OSError, ValueError):
                    pass
                next_save = time.monotonic() + 15
            time.sleep(.25)
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
