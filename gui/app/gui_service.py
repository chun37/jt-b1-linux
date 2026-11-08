"""GUI supervisor. Startup opens Spotify; normal close returns to tile home.
A separate owned worker attempts autoplay once per OS boot.
"""
import argparse
import fcntl
import json
import os
import select
import signal
import subprocess
import time
from pathlib import Path
import display_preview as surface
import system_shell
import standby_screen
from physical_buttons import Buttons

BUTTONS = None
from auth_helper import atomic_json, private_directory
from spotify_backend import cancel_orphan_controls, STATE

PY='/opt/jtb1-gui/bin/jtb1-gui-python'
APP='/opt/jtb1-gui/app/live_gui.py'
RUN=Path('/run/jtb1-gui')


def alive_gui(pid):
    try:return os.stat('/proc/'+str(pid)).st_uid==0 and APP.encode() in Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')
    except OSError:return False


def recover_stale():
    metadata=RUN/'recovery.json'
    if not metadata.exists():return True
    try:pid=int((RUN/'pid').read_text())
    except (OSError,ValueError):pid=None
    if pid and alive_gui(pid):return False
    if pid:
        try:cancel_orphan_controls(pid)
        except Exception:pass
    record=json.loads(metadata.read_text())
    surface.restore(record['session'])
    return True


def tile_action(tapped, origin):
    """Both ends must be in the same tile. Bar/blank-space taps do nothing."""
    if tapped is None or origin is None:
        return None
    try:
        points = [system_shell.app_point(*system_shell.panel_to_portrait(*p, system_shell.ORIENTATION))
                  for p in (tapped, origin)]
    except (ValueError, TypeError):
        return None
    if not all(p is not None for p in points):
        return None
    target, start = [standby_screen.launch_target(*p) for p in points]
    return target if target and target == start else None


def show_home(stopping):
    atomic_json(RUN/'service.json', {'state': 'hidden', 'screen': 'home', 'reopen': 'tile',
                                    'manager_pid': os.getpid()})
    while not stopping[0]:
        try:
            if wait_touch(stopping) == 'spotify':return
        except (OSError, ValueError, RuntimeError):
            # A broken input device must never be treated as an app selection.
            time.sleep(.25)


def wait_touch(stopping):
    fd,x,y=surface.open_touch()
    touch=surface.Touch(x,y)
    try:
        while not stopping[0]:
            system_shell.ensure()
            if BUTTONS is not None:BUTTONS.pump()
            ready,_,_=select.select([fd],[],[],0.25)
            if not ready:continue
            raw=os.read(fd,surface.EVENT.size*64)
            if not raw or len(raw)%surface.EVENT.size:
                raise ValueError('incomplete home input event')
            for event in surface.EVENT.iter_unpack(raw):
                tapped=touch.feed(*event[2:])
                if tile_action(tapped, touch.origin) == 'spotify':return 'spotify'
    finally:os.close(fd)


def main():
    global BUTTONS
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--duration',type=int,default=2147483647)
    args=parser.parse_args()
    os.umask(0o077)
    private_directory(RUN);private_directory(STATE)
    surface.RUN=RUN
    lock=os.open(RUN/'manager.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    atomic_json(RUN/'manager.json',{'pid':os.getpid(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()})
    stopping=[False]
    def stop(signum,frame):stopping[0]=True
    for number in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(number,stop)
    BUTTONS = Buttons()
    boot_worker = None
    if not args.once:
        with (STATE/'boot-playback.log').open('ab') as log:
            os.fchmod(log.fileno(),0o600)
            boot_worker = subprocess.Popen([PY, '/opt/jtb1-gui/app/boot_playback.py'],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        while not stopping[0]:
            try:
                if not recover_stale():
                    time.sleep(1);continue
                if not Path('/run/jtb1-display-ready').exists():raise RuntimeError('display not ready')
                system_shell.ensure()
                fd,x,y=surface.open_touch();os.close(fd)
            except Exception:
                if args.once:raise
                time.sleep(1);continue
            atomic_json(RUN/'service.json', {'state': 'app', 'manager_pid': os.getpid()})
            session='live-'+os.urandom(8).hex()
            logpath=STATE/'gui.log'
            if logpath.exists() and logpath.stat().st_size>1024*1024:os.replace(logpath,STATE/'gui.previous.log')
            with logpath.open('ab') as log:
                os.fchmod(log.fileno(),0o600)
                process=subprocess.Popen(['/bin/busybox','nice','-n','10',PY,APP,'--session',session,'--duration',str(args.duration)],
                                          stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            atomic_json(RUN/'child.json',{'pid':process.pid,'session':session})
            started=time.monotonic()
            home_requested=False
            try:
                while process.poll() is None and not stopping[0]:
                    # Independent of Pillow/native code: recover a stuck child.
                    status=RUN/'status.json'
                    if time.monotonic()-started>45 and (not status.exists() or time.time()-status.stat().st_mtime>45):
                        break
                    system_shell.ensure()
                    if BUTTONS.pump():
                        home_requested=True
                        break
                    time.sleep(0.25)
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=35)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill();process.wait(timeout=5)
                try:cancel_orphan_controls(process.pid)
                finally:surface.restore(session)
                (RUN/'child.json').unlink(missing_ok=True)
            if args.once:return process.returncode
            if stopping[0]:break
            if home_requested or process.returncode==0:
                try:show_home(stopping)
                except Exception:time.sleep(1)
            else:
                # Avoid a tight crash loop while preserving the former display.
                time.sleep(3)
    finally:
        if boot_worker is not None:
            if boot_worker.poll() is None:
                boot_worker.terminate()
                try:boot_worker.wait(timeout=35)
                except subprocess.TimeoutExpired:boot_worker.kill();boot_worker.wait(timeout=5)
            cancel_orphan_controls(boot_worker.pid)
        if BUTTONS is not None:BUTTONS.close()
        (RUN/'manager.json').unlink(missing_ok=True)
        os.close(lock)


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print('GUI supervisor could not continue; prior display recovery attempted.',flush=True)
        raise SystemExit(1)
