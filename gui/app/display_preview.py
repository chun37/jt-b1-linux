"""Read-only portrait display/touch pilot. NO playback controls or Web API tokens.

Catalog pages are explicitly labelled snapshots, not a finished search GUI.
A shell supervisor must invoke --restore after termination, including a C crash.
"""
import argparse
import array
import fcntl
import json
import mmap
import os
import queue
import select
import signal
import struct
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from portrait_geometry import WIDTH, HEIGHT, image_to_panel, panel_to_portrait

BASE = Path('/opt/jtb1-gui')
RUN = Path('/run/jtb1-gui-preview')
BG = '#10151c'
FG = '#f4f6f8'
DIM = '#a4b1be'
GREEN = '#56dda2'
WRITERS = ('/usr/local/sbin/jtb1-network-screen', '/usr/local/libexec/jtb1-touch-feedback')
EVENT = struct.Struct('<iiHHi')


def matching_writer(pid, target):
    try:
        return os.stat('/proc/' + str(pid)).st_uid == 0 and target.encode() in Path('/proc/' + str(pid) + '/cmdline').read_bytes().split(b'\0')
    except OSError:
        return False


def discover_writers():
    found = []
    for directory in Path('/proc').glob('[0-9]*'):
        for target in WRITERS:
            if matching_writer(int(directory.name), target):
                found.append({'pid': int(directory.name), 'target': target})
    if sorted(x['target'] for x in found) != sorted(WRITERS):
        raise RuntimeError('expected exactly one instance of both existing display writers')
    return found


def open_fb():
    if struct.calcsize('P') != 4:
        raise RuntimeError('real framebuffer access is restricted to the 32-bit device')
    fd = os.open('/dev/fb0', os.O_RDWR | os.O_CLOEXEC)
    try:
        variable = array.array('I', [0] * 40)
        fixed = bytearray(80)
        fcntl.ioctl(fd, 0x4600, variable, True)
        fcntl.ioctl(fd, 0x4602, fixed, True)
        if list(variable[:2]) != [1024, 600] or list(variable[4:7]) != [0, 0, 32]:
            raise RuntimeError('unexpected framebuffer geometry')
        if (variable[8], variable[9], variable[11], variable[12], variable[14], variable[15]) != (16, 8, 8, 8, 0, 8):
            raise RuntimeError('unexpected framebuffer channels')
        if struct.unpack_from('I', fixed, 44)[0] != 4096 or struct.unpack_from('I', fixed, 20)[0] < 4096 * 600:
            raise RuntimeError('unexpected framebuffer stride or allocation')
        memory = mmap.mmap(fd, 4096 * 600, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE)
        return fd, memory
    except BaseException:
        os.close(fd)
        raise


def save_private(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def restore(session=None):
    metadata = RUN / 'recovery.json'
    if not metadata.exists():
        return
    record = json.loads(metadata.read_text())
    if session is not None and record.get('session') != session:
        return
    if record['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        raise RuntimeError('refusing stale recovery data')
    try:
        backup = (RUN / 'original.bgra').read_bytes()
        if len(backup) != 4096 * 600:
            raise RuntimeError('invalid recovery framebuffer size')
        fd, memory = open_fb()
        try:
            memory[:] = backup
            if memory[:] != backup:
                raise RuntimeError('framebuffer recovery readback mismatch')
            print('GUI_FRAMEBUFFER_RESTORE_MATCH=1', flush=True)
        finally:
            memory.close()
            os.close(fd)
    finally:
        # Even a failed framebuffer restore must not leave boot services stopped.
        for writer in reversed(record['writers']):
            if matching_writer(writer['pid'], writer['target']):
                try:
                    os.kill(writer['pid'], signal.SIGCONT)
                except ProcessLookupError:
                    pass
    metadata.unlink()
    (RUN / 'original.bgra').unlink(missing_ok=True)
    (RUN / 'pid').unlink(missing_ok=True)
    save_private(RUN / 'status.json', json.dumps({'active': False, 'session': record.get('session'), 'read_only': True}).encode())


def open_touch():
    for path in sorted(Path('/dev/input').glob('event*')):
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            name = bytearray(128)
            fcntl.ioctl(fd, 0x80804506, name, True)
            if name.split(b'\0')[0] != b'Atmel maXTouch Touchscreen':
                os.close(fd)
                continue
            axes = []
            for request in (0x80184575, 0x80184576):
                data = array.array('i', [0] * 6)
                fcntl.ioctl(fd, request, data, True)
                axes.append(data.tolist())
            if (axes[0][1:3], axes[1][1:3]) != ([0, 1023], [0, 599]):
                raise RuntimeError('unexpected touch ranges')
            return fd, axes[0][0], axes[1][0]
        except OSError:
            os.close(fd)
    raise RuntimeError('touch device unavailable')


class Touch:
    def __init__(self, x=0, y=0):
        self.x, self.y = x, y
        self.slot = 0
        self.down = self.last_down = self.dropped = False
        self.origin = None
        self.distance = 0
        self.gesture = None

    def feed(self, kind, code, value):
        self.gesture = None
        if kind == 0 and code == 3:
            self.dropped = True
            self.down = self.last_down = False
            self.origin = None
            return None
        if self.dropped:
            if kind == 0 and code == 0:
                self.dropped = False
                self.slot = 0
            return None
        if kind == 3:
            if code == 47:
                self.slot = value
            elif self.slot == 0:
                if code == 57:
                    self.down = value >= 0
                elif code == 53:
                    self.x = max(0, min(1023, value))
                elif code == 54:
                    self.y = max(0, min(599, value))
        if kind == 0 and code == 0:
            if self.down and not self.last_down:
                self.origin = (self.x, self.y)
                self.distance = 0
            if self.down and self.origin:
                dx, dy = self.x - self.origin[0], self.y - self.origin[1]
                self.distance = max(self.distance, dx * dx + dy * dy)
            tap = (self.x, self.y) if self.last_down and not self.down and self.distance <= 24 * 24 else None
            if self.last_down and not self.down and self.origin and self.distance > 24 * 24:
                self.gesture = (self.origin, (self.x, self.y))
            self.last_down = self.down
            return tap
        return None


def playback_worker(output, stopping):
    while not stopping.is_set():
        process = None
        try:
            # Only this fixed READ command is permitted in the display pilot.
            process = subprocess.Popen(['/usr/local/sbin/jtb1-soloist', 'ctl', 'now', '--json'],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            raw, _ = process.communicate(timeout=4)
            if process.returncode == 0:
                value = json.loads(raw)
                try:
                    output.get_nowait()
                except queue.Empty:
                    pass
                output.put_nowait(value)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
        stopping.wait(2)


def text(value):
    return ''.join(c for c in str(value or '') if c.isprintable())[:300]


class View:
    def __init__(self, font, catalog):
        self.fonts = {size: ImageFont.truetype(str(font), size, index=0) for size in (18, 22, 28, 36)}
        self.catalog = catalog
        self.page = 'now'
        self.orientation = 'cw'
        self.marker = None
        self.state = {}

    def fitted(self, value, size, width):
        value = text(value)
        if self.fonts[size].getlength(value) <= width:
            return value
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            if self.fonts[size].getlength(value[:middle] + '…') <= width:
                low = middle
            else:
                high = middle - 1
        return value[:low] + '…'

    def render(self):
        image = Image.new('RGBA', (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(image)
        def label(x, y, value, size=22, fill=FG, width=550):
            draw.text((x, y), self.fitted(value, size, width), font=self.fonts[size], fill=fill)
        def box(bounds, value, fill='#253341'):
            draw.rounded_rectangle(bounds, radius=12, fill=fill)
            label(bounds[0] + 14, bounds[1] + 10, value, 22, width=bounds[2] - bounds[0] - 22)
        label(24, 12, '縦画面の表示試作', 22, GREEN, 350)
        box((390, 4, 484, 59), '反転')
        box((494, 4, 594, 59), '終了')
        label(24, 68, '表示確認中：再生や音量は操作しません', 18, DIM)
        if self.marker:
            label(24, 104, 'タッチ：' + str(self.marker[0]) + ', ' + str(self.marker[1]), 18, GREEN)
        if self.page == 'now':
            label(24, 145, '再生中', 36)
            draw.ellipse((150, 226, 450, 526), fill='#203d39', outline=GREEN, width=5)
            draw.ellipse((245, 321, 355, 431), fill=BG, outline=GREEN, width=4)
            item = (self.state.get('item') or {}).get('decorations') or {}
            title = item.get('identity', {}).get('name', '再生情報を読み取り中')
            creators = item.get('creators', [])
            artist = ' / '.join(x.get('entity', {}).get('decorations', {}).get('identity', {}).get('name', '') for x in creators)
            label(24, 565, title, 28)
            label(24, 615, artist, 22, DIM)
            names = {'playing': '再生中', 'paused': '一時停止', 'buffering': '読み込み中', 'idle': '待機'}
            label(24, 679, 'Soloist：' + names.get(self.state.get('status'), '接続確認中'), 22, GREEN)
            label(24, 733, '音声は既存のSoloistで継続しています', 18, DIM)
            box((24, 795, 576, 864), '再生操作は表示確認後に接続します')
        else:
            headings = {'search': '検索テスト「初音ミク」', 'saved': '保存曲', 'playlists': 'プレイリスト'}
            label(24, 145, headings[self.page], 28)
            rows = self.catalog.get(self.page, [])
            total = self.catalog.get('totals', {}).get(self.page)
            label(24, 192, 'API取得済みデータの表示' + (' / 全' + str(total) + '件' if total is not None else ''), 18, DIM)
            for number, row in enumerate(rows[:5]):
                y = 242 + number * 108
                draw.rounded_rectangle((20, y, 580, y + 96), radius=14, fill='#1c2732')
                label(34, y + 12, row.get('title'), 22, width=525)
                label(34, y + 50, row.get('subtitle'), 18, DIM, 525)
            label(24, 808, '先頭5件のスナップショットです', 18, DIM)
            label(24, 848, '入力、続きの取得、再生操作は未接続です', 18, DIM)
        for index, (key, name) in enumerate([('now', '再生中'), ('search', '検索'), ('saved', '保存曲'), ('playlists', 'リスト')]):
            left = index * 150
            draw.rectangle((left, 928, left + 149, 1023), fill='#284b43' if self.page == key else '#18232e')
            label(left + 27, 958, name, 22, GREEN if self.page == key else FG, 120)
        if self.marker and time.monotonic() < self.marker[2]:
            x, y = self.marker[:2]
            draw.ellipse((x - 16, y - 16, x + 16, y + 16), outline=GREEN, width=4)
        return image

    def tap(self, x, y):
        self.marker = (x, y, time.monotonic() + 2)
        if y < 60:
            if x >= 494:
                return False
            if 390 <= x <= 484:
                self.orientation = 'ccw' if self.orientation == 'cw' else 'cw'
        elif y >= 928:
            self.page = ('now', 'search', 'saved', 'playlists')[min(3, x // 150)]
        return True


def run(font, catalog_file, duration, session, view_factory=None):
    if view_factory is not None:
        from system_shell import run_client
        return run_client(font, catalog_file, duration, session, view_factory)
    os.umask(0o077)
    RUN.mkdir(mode=0o700, exist_ok=True)
    lock = os.open(RUN / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (RUN / 'recovery.json').exists():
        raise RuntimeError('unfinished recovery; restore first')
    view = (view_factory or View)(font, json.loads(catalog_file.read_text()))
    fd, memory = open_fb()
    touch_fd = None
    stopping = threading.Event()
    updates = queue.Queue(maxsize=1)
    worker = None
    try:
        writers = discover_writers()
        touch_fd, x, y = open_touch()
        touch = Touch(x, y)
        # Stop only the two established framebuffer writers, never audio,
        # Wi-Fi, its DHCP/supplicant, battery LED, or the touch kernel driver.
        record = {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'writers': writers, 'session': session}
        # Publish a valid recovery snapshot BEFORE stopping anyone. A SIGKILL
        # at any later instruction must still be recoverable by the supervisor.
        save_private(RUN / 'original.bgra', memory[:])
        save_private(RUN / 'recovery.json', json.dumps(record).encode())
        for writer in writers:
            if not matching_writer(writer['pid'], writer['target']):
                raise RuntimeError('display writer changed during preparation')
            os.kill(writer['pid'], signal.SIGSTOP)
        # The stopped network shell cannot spawn more painters. Account for a
        # network-text child which might already have been running at SIGSTOP.
        parent = next(w['pid'] for w in writers if w['target'] == WRITERS[0])
        target = '/usr/local/libexec/jtb1-network-text'
        for directory in Path('/proc').glob('[0-9]*'):
            pid = int(directory.name)
            try:
                parent_id = int((directory / 'stat').read_text().rsplit(')', 1)[1].split()[1])
                if parent_id != parent or not matching_writer(pid, target):
                    continue
                record['writers'].append({'pid': pid, 'target': target})
                save_private(RUN / 'recovery.json', json.dumps(record).encode())
                if matching_writer(pid, target):
                    os.kill(pid, signal.SIGSTOP)
            except (FileNotFoundError, ProcessLookupError):
                pass
        # Replace, never truncate, the recovery snapshot after all painters stop.
        save_private(RUN / 'original.bgra', memory[:])
        (RUN / 'pid').write_text(str(os.getpid()))
        worker = threading.Thread(target=playback_worker, args=(updates, stopping), daemon=True)
        worker.start()
        def finish(signum, frame):
            stopping.set()
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(number, finish)
        until = time.monotonic() + duration
        last_draw = last_status = 0
        dirty = True
        last_marker_active = False
        frames = 0
        while not stopping.is_set() and time.monotonic() < until:
            if hasattr(view, 'tick') and view.tick():
                dirty = True
            try:
                view.state = updates.get_nowait()
                dirty = True
            except queue.Empty:
                pass
            readable, _, _ = select.select([touch_fd], [], [], 0.08)
            if readable:
                dirty = True
                raw = os.read(touch_fd, EVENT.size * 128)
                if len(raw) % EVENT.size:
                    raise RuntimeError('partial input event')
                for event in EVENT.iter_unpack(raw):
                    tapped = touch.feed(*event[2:])
                    if touch.down:
                        px, py = panel_to_portrait(touch.x, touch.y, view.orientation)
                        view.marker = (px, py, time.monotonic() + 2)
                    if touch.gesture and hasattr(view, 'swipe'):
                        origin, end = touch.gesture
                        view.swipe(panel_to_portrait(*origin, view.orientation), panel_to_portrait(*end, view.orientation))
                    if tapped:
                        px, py = panel_to_portrait(*tapped, view.orientation)
                        if not view.tap(px, py):
                            stopping.set()
            now = time.monotonic()
            marker_active = bool(view.marker and now < view.marker[2])
            dirty = dirty or marker_active != last_marker_active
            if dirty and now - last_draw >= 0.25:
                memory[:] = image_to_panel(view.render(), view.orientation).tobytes('raw', 'BGRA')
                last_draw = now
                last_marker_active = marker_active
                dirty = False
                frames += 1
            if now - last_status >= 1:
                save_private(RUN / 'status.json', json.dumps({'page': view.page, 'orientation': view.orientation,
                    'touch': list(view.marker[:2]) if view.marker else None, 'playback': view.state.get('status', 'unknown'),
                    'read_only': getattr(view, 'read_only', True), 'active': True, 'session': session, 'frames': frames, 'updated_at': int(time.time()),
                    **(view.snapshot() if hasattr(view, 'snapshot') else {})}).encode())
                last_status = now
    finally:
        stopping.set()
        if hasattr(view, 'close'):
            try:
                view.close()
            except Exception:
                pass
        if worker:
            worker.join(timeout=6)
        memory.close(); os.close(fd)
        if touch_fd is not None:
            os.close(touch_fd)
        restore()
        (RUN / 'pid').unlink(missing_ok=True)
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--font', type=Path, default=BASE / 'share/NotoSansCJK-Regular.ttc')
    parser.add_argument('--catalog', type=Path, default=BASE / 'data/preview.json')
    parser.add_argument('--duration', type=int, default=600)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    if args.restore:
        restore(args.session)
    else:
        run(args.font, args.catalog, args.duration, args.session)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Do not log track metadata, credentials, or arbitrary exception strings.
        print('GUI display pilot stopped; inspect recovery/status files, not credentials.', flush=True)
        raise SystemExit(1)
