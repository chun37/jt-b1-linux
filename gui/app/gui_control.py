"""Manual GUI lifecycle. Does not modify boot configuration or stop audio."""
import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from auth_helper import private_directory
from spotify_backend import STATE
RUN=Path('/run/jtb1-gui')
PY='/opt/jtb1-gui/bin/jtb1-gui-python'
SERVICE='/opt/jtb1-gui/app/gui_service.py'


def manager_pid():
    try:
        value=json.loads((RUN/'manager.json').read_text())
        pid=value['pid']
        if type(pid) is not int or pid<=1:return None
        if value['boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():return None
        if os.stat('/proc/'+str(pid)).st_uid!=0:return None
        if SERVICE.encode() not in Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0'):return None
        return pid
    except (OSError,ValueError,KeyError):return None


def home_ready(pid):
    try:
        home = json.loads((RUN/'service.json').read_text())
        shell = json.loads(Path('/run/jtb1-shell/status.json').read_text())
        return (home.get('manager_pid') == pid and home.get('screen') == 'home'
                and home.get('state') == 'hidden' and shell.get('active')
                and not shell.get('app_active') and 0 <= time.time() - shell.get('updated_at', 0) < 5)
    except (OSError, ValueError, TypeError):
        return False


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','stop','status','run'])
    action=parser.parse_args().action
    if action=='run':os.execv(PY,[PY,SERVICE])
    if action=='status':
        print('GUI_MANAGER_RUNNING='+str(int(manager_pid() is not None)))
        try:
            value=json.loads((RUN/'status.json').read_text())
            print(json.dumps({key:value.get(key) for key in ('active','page','orientation','read_only','api_connected','loading','control_busy')},ensure_ascii=True))
        except (OSError,ValueError):print('GUI_STATUS_UNAVAILABLE')
        return
    if action=='stop':
        pid=manager_pid()
        if pid:
            os.kill(pid,signal.SIGTERM)
            deadline=time.monotonic()+45
            while manager_pid()==pid and time.monotonic()<deadline:time.sleep(.1)
            if manager_pid()==pid:raise RuntimeError('stop deadline')
        print('GUI_STOPPED_AUDIO_PRESERVED=1');return
    private_directory(STATE)
    if manager_pid() is None:
        with (STATE/'supervisor.log').open('ab') as log:
            os.fchmod(log.fileno(),0o600)
            process=subprocess.Popen([PY,SERVICE],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('manager failed')
            if manager_pid()==process.pid and home_ready(process.pid):
                print('GUI_MANAGER_STARTED=1; HOME_SCREEN_READY=1');return
            try:
                status=json.loads((RUN/'status.json').read_text())
                child=json.loads((RUN/'child.json').read_text())
                if manager_pid()==process.pid and status.get('active') and status.get('session')==child['session']:
                    print('GUI_MANAGER_STARTED=1; LIVE_SCREEN_READY=1');return
            except (OSError,ValueError,KeyError):pass
            time.sleep(.1)
        raise RuntimeError('ready deadline')
    print('GUI_MANAGER_ALREADY_RUNNING=1')


if __name__=='__main__':
    try:main()
    except Exception:
        print('GUI control did not finish; inspect safe status, not credentials.',flush=True)
        raise SystemExit(1)
