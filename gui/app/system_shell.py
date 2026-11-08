"""Portrait system compositor: sole framebuffer owner, app-independent status bar.

App protocol v1: publish a 600x980 RGBA image using AppSurface.present().
Input: logical screen 600x1024; app origin (0,44). Status bar is not app input.
Legacy Spotify is scaled vertically from 1024 to 980 by run_client().
"""
import argparse
import fcntl
import json
import os
import queue
import select
import signal
import subprocess
import threading
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import display_preview as surface
from portrait_geometry import image_to_panel, panel_to_portrait
import system_status
import standby_screen

ROOT = Path('/run/jtb1-shell')
SCRIPT = '/opt/jtb1-gui/app/system_shell.py'
PY = '/opt/jtb1-gui/bin/jtb1-gui-python'
WIDTH, HEIGHT, BAR, APP_HEIGHT = 600, 1024, 44, 980
ORIENTATION = 'ccw'


def identity(pid):
    return Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()[19]


def alive(record):
    try:
        return type(record['pid']) is int and record['pid'] > 1 and identity(record['pid']) == record['start']
    except (OSError, KeyError, ValueError, TypeError):
        return False


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(path, value):
    surface.save_private(path, json.dumps(value).encode())


def running():
    record = load(ROOT / 'daemon.json')
    if not alive(record):
        return False
    try:
        return SCRIPT.encode() in Path('/proc/' + str(record['pid']) + '/cmdline').read_bytes().split(b'\0')
    except OSError:
        return False


def ensure():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if running():
        return
    with (ROOT / 'daemon.log').open('ab') as log:
        os.fchmod(log.fileno(), 0o600)
        process = subprocess.Popen([PY, SCRIPT, 'run'], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if process.poll() is not None:
            # A competing ensure may have won the exclusive daemon lock.
            if running():
                return
            raise RuntimeError('system compositor failed')
        if running():
            return
        time.sleep(.1)
    raise TimeoutError('system compositor not ready')


class AppSurface:
    """Single active app, atomic frame delivery. Does not open /dev/fb0."""
    def __init__(self, name='app'):
        ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fd = os.open(ROOT / 'app.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            raise
        self.owner = {'pid': os.getpid(), 'start': identity(os.getpid()), 'name': name, 'protocol': 1}
        # Retire the previous owner's frame before publishing the new owner.
        (ROOT / 'frame.rgba').unlink(missing_ok=True)
        save(ROOT / 'app.json', self.owner)

    def present(self, image):
        if image.size != (WIDTH, APP_HEIGHT):
            raise ValueError('app viewport must be 600x980')
        surface.save_private(ROOT / 'frame.rgba', image.convert('RGBA').tobytes())

    def close(self):
        try:
            if load(ROOT / 'app.json') == self.owner:
                (ROOT / 'app.json').unlink(missing_ok=True)
        finally:
            os.close(self.fd)


def app_point(x, y, legacy=False):
    if not (0 <= x < WIDTH and BAR <= y < HEIGHT):
        return None
    return x, min(1023, int((y - BAR) * 1024 / APP_HEIGHT)) if legacy else y - BAR


def claim_display(memory):
    writers = surface.discover_writers()
    record = {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'writers': writers, 'session': 'system-shell'}
    # Add start-time validation to existing surface recovery.
    for writer in writers:
        writer['start'] = identity(writer['pid'])
    surface.save_private(ROOT / 'original.bgra', memory[:])
    save(ROOT / 'recovery.json', record)
    for writer in writers:
        if not surface.matching_writer(writer['pid'], writer['target']) or not alive(writer):
            raise RuntimeError('display writer changed')
        os.kill(writer['pid'], signal.SIGSTOP)
    # Wait until the parent is stopped before enumerating existing painter children.
    end = time.monotonic() + 2
    while time.monotonic() < end:
        if all(Path('/proc/' + str(w['pid']) + '/stat').read_text().rsplit(')', 1)[1].split()[0] == 'T' for w in writers):
            break
        time.sleep(.01)
    else:
        raise RuntimeError('display writers did not stop')
    parent = next(w['pid'] for w in writers if w['target'] == surface.WRITERS[0])
    target = '/usr/local/libexec/jtb1-network-text'
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            pid = int(directory.name)
            parent_id = int((directory / 'stat').read_text().rsplit(')', 1)[1].split()[1])
            if parent_id == parent and surface.matching_writer(pid, target):
                writer = {'pid': pid, 'target': target, 'start': identity(pid)}
                record['writers'].append(writer)
                save(ROOT / 'recovery.json', record)
                if alive(writer):
                    os.kill(pid, signal.SIGSTOP)
        except (FileNotFoundError, ProcessLookupError):
            pass
    surface.save_private(ROOT / 'original.bgra', memory[:])


def recover():
    # Filter reused PIDs before handing recovery to the established mechanism.
    record = load(ROOT / 'recovery.json')
    if record:
        record['writers'] = [w for w in record['writers'] if alive(w)]
        save(ROOT / 'recovery.json', record)
        surface.restore('system-shell')


def daemon():
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(ROOT / 'daemon.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    surface.RUN = ROOT
    recover()
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *args: stopping.set())
    fd, memory = surface.open_fb()
    worker = None
    try:
        claim_display(memory)
        font = surface.BASE / 'share/NotoSansCJK-Regular.ttc'
        fonts = {size: ImageFont.truetype(str(font), size, index=0) for size in (18, 22, 28)}
        updates = queue.Queue(maxsize=1)
        def collect():
            while not stopping.is_set():
                try:
                    state = system_status.collect()
                    if updates.full():
                        updates.get_nowait()
                    updates.put_nowait(state)
                except Exception:
                    pass
                stopping.wait(10)
        worker = threading.Thread(target=collect, daemon=True)
        worker.start()
        placeholder = standby_screen.render_body(fonts)
        state, previous, frames, stamp = {}, None, 0, 0
        app_image, frame_stamp = placeholder, None
        while not stopping.is_set():
            try:
                state = updates.get_nowait()
            except queue.Empty:
                pass
            owner = load(ROOT / 'app.json')
            active = alive(owner)
            try:
                info = (ROOT / 'frame.rgba').stat() if active else None
                current_stamp = (info.st_mtime_ns, owner.get('pid'), owner.get('start')) if info else None
                if current_stamp != frame_stamp:
                    raw = (ROOT / 'frame.rgba').read_bytes() if info else b''
                    app_image = Image.frombytes('RGBA', (WIDTH, APP_HEIGHT), raw) if len(raw) == WIDTH * APP_HEIGHT * 4 else placeholder
                    frame_stamp = current_stamp
            except (OSError, ValueError):
                app_image, frame_stamp = placeholder, None
            key = (frame_stamp, int(time.time() // 60), state.get('updated_at'), time.time() - state.get('updated_at', 0) > 30)
            if key != previous:
                image = Image.new('RGBA', (WIDTH, HEIGHT))
                image.paste(app_image, (0, BAR))
                image.paste(system_status.render_bar(fonts, state), (0, 0))
                memory[:] = image_to_panel(image, ORIENTATION).tobytes('raw', 'BGRA')
                frames += 1
                previous = key
            if time.monotonic() - stamp >= 1:
                save(ROOT / 'status.json', {'active': True, 'orientation': ORIENTATION, 'bar_height': BAR,
                     'viewport': [WIDTH, APP_HEIGHT], 'app_active': active, 'frames': frames,
                     'indicators': state, 'updated_at': time.time()})
                save(ROOT / 'daemon.json', {'pid': os.getpid(), 'start': identity(os.getpid())})
                stamp = time.monotonic()
            stopping.wait(.1)
    finally:
        stopping.set()
        (ROOT / 'daemon.json').unlink(missing_ok=True)
        if worker:
            worker.join(timeout=6)
        memory.close()
        os.close(fd)
        recover()
        save(ROOT / 'status.json', {'active': False})
        os.close(lock)


def run_client(font, catalog_file, duration, session, view_factory):
    """Legacy Spotify adapter. App lifecycle is independent of the compositor."""
    ensure()
    surface.RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(surface.RUN / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    app = AppSurface('Spotify')
    view, touch_fd, worker = None, None, None
    stopping = threading.Event()
    updates = queue.Queue(maxsize=1)
    try:
        view = view_factory(font, json.loads(catalog_file.read_text()))
        view.orientation = ORIENTATION
        touch_fd, x, y = surface.open_touch()
        touch = surface.Touch(x, y)
        (surface.RUN / 'pid').write_text(str(os.getpid()))
        worker = threading.Thread(target=surface.playback_worker, args=(updates, stopping), daemon=True)
        worker.start()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *args: stopping.set())
        until, last_draw, last_status = time.monotonic() + duration, 0, 0
        dirty, frames, last_marker = True, 0, False
        while not stopping.is_set() and time.monotonic() < until:
            if hasattr(view, 'tick') and view.tick():
                dirty = True
            try:
                view.state = updates.get_nowait()
                dirty = True
            except queue.Empty:
                pass
            if select.select([touch_fd], [], [], .08)[0]:
                raw = os.read(touch_fd, surface.EVENT.size * 128)
                if len(raw) % surface.EVENT.size:
                    raise ValueError('partial input event')
                for event in surface.EVENT.iter_unpack(raw):
                    tapped = touch.feed(*event[2:])
                    if touch.down:
                        point = app_point(*panel_to_portrait(touch.x, touch.y, ORIENTATION), legacy=True)
                        if point:
                            view.marker = (*point, time.monotonic() + .5)
                            dirty = True
                    if touch.gesture and hasattr(view, 'swipe'):
                        points = [app_point(*panel_to_portrait(*p, ORIENTATION), legacy=True) for p in touch.gesture]
                        if all(points):
                            view.swipe(*points)
                            dirty = True
                    if tapped:
                        point = app_point(*panel_to_portrait(*tapped, ORIENTATION), legacy=True)
                        origin = app_point(*panel_to_portrait(*touch.origin, ORIENTATION), legacy=True) if touch.origin else None
                        if point and origin:
                            dirty = True
                            if not view.tap(*point):
                                stopping.set()
            now = time.monotonic()
            marker = bool(view.marker and now < view.marker[2])
            dirty = dirty or marker != last_marker
            if dirty and now - last_draw >= .25:
                app.present(view.render().resize((WIDTH, APP_HEIGHT), Image.Resampling.LANCZOS))
                frames += 1
                last_draw, dirty, last_marker = now, False, marker
            if now - last_status >= 1:
                surface.save_private(surface.RUN / 'status.json', json.dumps({
                    'page': view.page, 'orientation': ORIENTATION, 'touch': list(view.marker[:2]) if view.marker else None,
                    'playback': view.state.get('status', 'unknown'), 'active': True, 'session': session,
                    'read_only': getattr(view, 'read_only', True), 'frames': frames, 'updated_at': int(time.time()),
                    'system_shell': True, 'viewport': [WIDTH, APP_HEIGHT],
                    **(view.snapshot() if hasattr(view, 'snapshot') else {})}).encode())
                last_status = now
    finally:
        stopping.set()
        try:
            if view is not None and hasattr(view, 'close'):
                view.close()
        finally:
            if worker:
                worker.join(timeout=6)
            if touch_fd is not None:
                os.close(touch_fd)
            app.close()
            (surface.RUN / 'pid').unlink(missing_ok=True)
            surface.save_private(surface.RUN / 'status.json', json.dumps({'active': False, 'session': session, 'system_shell': True}).encode())
            os.close(lock)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('run', 'start', 'stop', 'status'))
    action = parser.parse_args().action
    if action == 'run':
        daemon()
    elif action == 'start':
        ensure()
    elif action == 'status':
        print(json.dumps(load(ROOT / 'status.json'), ensure_ascii=False))
    elif running():
        record = load(ROOT / 'daemon.json')
        os.kill(record['pid'], signal.SIGTERM)
        end = time.monotonic() + 12
        while alive(record) and time.monotonic() < end:
            time.sleep(.1)
        if alive(record):
            raise TimeoutError('compositor stop timed out')


if __name__ == '__main__':
    main()
