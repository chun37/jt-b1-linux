"""Standalone Spotify metadata/refresh client and bounded local Soloist controls.
No secret-bearing exception bodies, URLs, command arguments, or response logs.
"""
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from auth_helper import atomic_json, private_directory, validated_token, AuthFailure

# Each reader must register their own Spotify Web API application.
# A public Client ID is not a secret, but the author's ID is not distributed.
def configured_client_id():
    value = os.environ.get('JTB1_SPOTIFY_CLIENT_ID')
    if value is None:
        try:
            value = Path('/etc/jtb1-gui/client-id').read_text().strip()
        except OSError:
            return ''
    if not re.fullmatch(r'[0-9a-fA-F]{32}', value):
        raise ValueError('JTB1_SPOTIFY_CLIENT_ID must be a 32-character hex Client ID')
    return value


CLIENT_ID = configured_client_id()
STATE = Path('/var/lib/jtb1-gui')
API = 'https://api.spotify.com/v1'
TOKEN = 'https://accounts.spotify.com/api/token'
URI = re.compile(r'spotify:(track|album|artist|playlist|episode):([A-Za-z0-9]{22})\Z')


class BackendError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise BackendError('安全のためリダイレクトを拒否しました')


def opener():
    # Do not inherit an accidental proxy for private authorization traffic.
    return build_opener(ProxyHandler({}), NoRedirect())


def read_credential(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise BackendError('認証ファイルの権限を確認してください')
        value = json.load(stream)
    if value.get('client_id') != CLIENT_ID or type(value.get('expires_at')) is not int:
        raise BackendError('認証情報の形式を確認してください')
    # Reuse the initial strict token/scope validation without changing expiry.
    validated_token(dict(value, expires_in=3600), CLIENT_ID)
    return value


class WebAPI:
    def __init__(self, state=STATE, http=None, clock=time.time):
        self.state = Path(state)
        private_directory(self.state)
        self.cache = self.state / 'cache'
        private_directory(self.cache)
        self.http = http or opener()
        self.clock = clock
        self.cooldown = 0
        self.credential = self.state / 'web-api-token.json'

    def _json(self, request, cap=2*1024*1024):
        try:
            with self.http.open(request, timeout=12) as response:
                raw = response.read(cap + 1)
                if response.status != 200 or len(raw) > cap:
                    raise BackendError('Spotifyの応答を処理できません')
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise BackendError('Spotifyの応答形式が異なります')
                return result
        except HTTPError as error:
            code = error.code
            if code == 429:
                try:
                    delay = min(3600, max(1, int(error.headers.get('Retry-After', '30'))))
                except (ValueError, TypeError):
                    delay = 30
                self.cooldown = self.clock() + delay
            error.close()
            messages = {401:'認証の更新が必要です', 403:'Spotify APIの制限で表示できません',
                        404:'対象が見つかりません', 429:'Spotifyの利用制限中です。時間を置いて再試行してください'}
            raise BackendError(messages.get(code, 'Spotifyへの接続に失敗しました'), code) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise BackendError('ネットワーク接続を確認してください') from None

    def token(self, force=False):
        if not CLIENT_ID:
            raise BackendError('自分のSpotify Web API Client IDを設定してください')
        lock = os.open(self.state/'refresh.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = read_credential(self.credential)
            if not force and current['expires_at'] > self.clock() + 90:
                return current['access_token']
            payload = self._json(Request(TOKEN, data=urlencode({'grant_type':'refresh_token',
                        'client_id':CLIENT_ID, 'refresh_token':current['refresh_token']}).encode(),
                        headers={'Content-Type':'application/x-www-form-urlencoded'}), cap=65536)
            payload.setdefault('refresh_token', current['refresh_token'])
            payload.setdefault('scope', current['scope'])
            try:
                renewed = validated_token(payload, CLIENT_ID)
            except AuthFailure:
                raise BackendError('認証の更新を検証できません。PCで再認証が必要です') from None
            atomic_json(self.credential, renewed)
            return renewed['access_token']
        finally:
            os.close(lock)

    def get(self, path, params=None, fresh=False):
        if not re.fullmatch(r'/(me(?:/tracks|/playlists|/albums|/following)?|search|tracks/[A-Za-z0-9]{22}|albums/[A-Za-z0-9]{22}/tracks|artists/[A-Za-z0-9]{22}/albums|playlists/[A-Za-z0-9]{22}/items)', path):
            raise BackendError('許可されていないAPIパスです')
        url = API + path + ('?' + urlencode(params) if params else '')
        cached = self.cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        if not fresh and cached.exists() and self.clock() - cached.stat().st_mtime < 60:
            return json.loads(cached.read_text())
        if self.cooldown > self.clock():
            raise BackendError('Spotifyの利用制限中です。しばらくお待ちください', 429)
        token = self.token()
        try:
            result = self._json(Request(url, headers={'Authorization':'Bearer '+token,'Accept':'application/json'}))
        except BackendError as error:
            if error.status != 401:
                raise
            token = self.token(force=True)
            result = self._json(Request(url, headers={'Authorization':'Bearer '+token,'Accept':'application/json'}))
        atomic_json(cached, result)
        for old in sorted(self.cache.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[64:]:
            old.unlink(missing_ok=True)
        return result

    def page(self, kind, query='', offset=0, identifier='', fresh=False):
        params = {'limit':10, 'offset':max(0, int(offset))}
        if kind.startswith('search:'):
            entity = kind.split(':')[1]
            if entity not in ('track','album','artist','playlist') or not query.strip():
                raise BackendError('検索語を入力してください')
            params.update(q=query.strip()[:200], type=entity)
            result = self.get('/search', params, fresh).get(entity+'s') or {}
        elif kind == 'saved':
            result = self.get('/me/tracks', params, fresh)
        elif kind == 'playlists':
            result = self.get('/me/playlists', params, fresh)
        elif kind == 'albums':
            result = self.get('/me/albums', params, fresh)
        elif kind == 'artists':
            params = {'type':'artist','limit':10}
            if query:
                params['after'] = query
            result = self.get('/me/following', params, fresh).get('artists') or {}
        elif kind in ('playlist','album','artist'):
            if not re.fullmatch('[A-Za-z0-9]{22}', identifier):
                raise BackendError('対象の識別子が不正です')
            suffix = {'playlist':'/items','album':'/tracks','artist':'/albums'}[kind]
            result = self.get('/'+kind+'s/'+identifier+suffix, params, fresh)
        else:
            raise BackendError('未対応の一覧です')
        rows = []
        for entry in result.get('items') or []:
            if not isinstance(entry, dict):
                continue
            item = (entry.get('track') or entry.get('item') or entry.get('album') or entry)
            if not isinstance(item, dict):
                continue
            uri = item.get('uri', '')
            match = URI.fullmatch(uri)
            if not match:
                continue
            creators = ' / '.join(x.get('name','') for x in item.get('artists') or [] if isinstance(x,dict))
            rows.append({'title':item.get('name') or '名称なし', 'subtitle':creators or {'playlist':'プレイリスト','album':'アルバム','artist':'アーティスト'}.get(match[1],''),
                         'uri':uri,'kind':match[1],'id':match[2], 'playable':item.get('is_playable') is not False,
                         'images':item.get('images') or (item.get('album') or {}).get('images') or []})
        next_offset = None
        if result.get('next') and kind != 'artists':
            link = urlsplit(result['next'])
            if link.scheme != 'https' or link.hostname != 'api.spotify.com' or link.username or link.port not in (None,443):
                raise BackendError('ページの続きのURLを拒否しました')
            values = parse_qs(link.query).get('offset', [])
            if len(values)==1 and values[0].isdigit():
                next_offset = int(values[0])
        return {'rows':rows,'offset':offset,'next_offset':next_offset,'total':result.get('total'),
                'next_cursor':(result.get('cursors') or {}).get('after') if result.get('next') else None}


RPC = Path('/run/jtb1-gui-rpc')
VOLUME_LOCK = threading.local()


def process_start(pid):
    return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]


@contextmanager
def control_lease(command):
    path=None
    if command in ('play','next','prev','seek','volume'):
        private_directory(RPC)
        path=RPC/('action-'+str(os.getpid())+'.json')
        atomic_json(path,{'owner':os.getpid(),'command':command})
    try:yield
    finally:
        if path:path.unlink(missing_ok=True)


def cancel_orphan_controls(owner):
    """Called by the supervisor only after its GUI child has exited."""
    action=RPC/('action-'+str(owner)+'.json')
    active=action.exists()
    if RPC.exists():
        for path in RPC.glob('rpc-*.json'):
            try:
                lease=json.loads(path.read_text())
                if lease['owner']!=owner:continue
                pid=lease['pid']
                if process_start(pid)==lease['start'] and os.getpgid(pid)==pid:
                    os.killpg(pid,signal.SIGKILL)
                path.unlink(missing_ok=True)
            except (OSError,ValueError,KeyError):
                pass
    if active:
        try:ctl('pause')
        finally:action.unlink(missing_ok=True)
    return active


def ctl(command, argument=None, timeout=5):
    allowed = {'now','play','pause','next','prev','seek','volume','shuffle','repeat','activate'}
    if command not in allowed:
        raise BackendError('許可されていない再生操作です')
    args = ['/usr/local/sbin/jtb1-soloist','ctl',command]
    if command == 'now':
        args.append('--json')
    elif argument is not None:
        args.append(str(argument))
    lease=None
    gated=command!='now'
    if gated:
        private_directory(RPC)
        args=['/bin/sh','/opt/jtb1-gui/bin/cli-gate',command]+([str(argument)] if argument is not None else [])
    process = subprocess.Popen(args,stdin=subprocess.PIPE if gated else subprocess.DEVNULL,
                               stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,
                               pass_fds=((VOLUME_LOCK.fd,) if getattr(VOLUME_LOCK,'fd',None) is not None else ()))
    try:
        if gated:
            lease=RPC/('rpc-'+str(process.pid)+'.json')
            atomic_json(lease,{'owner':os.getpid(),'pid':process.pid,'start':process_start(process.pid),'command':command})
        output,_ = process.communicate(input=b'GO\n' if gated else None,timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid,signal.SIGTERM)
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            process.communicate()
        except ProcessLookupError:
            pass
        raise BackendError('再生操作が時間内に完了しませんでした') from None
    finally:
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.communicate()
        if lease:lease.unlink(missing_ok=True)
    if process.returncode:
        raise BackendError('Soloistへ接続できません')
    return json.loads(output) if command=='now' else None


def start_audio():
    """Lazy start only on explicit Play. Never replace a live/partial audio route."""
    private_directory(STATE)
    lock=os.open(STATE/'audio-start.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX)
        run=Path('/run/jtb1-soloist')
        if (run/'runner.pid').exists():
            try:
                os.kill(int((run/'runner.pid').read_text()),0)
                return
            except (OSError,ValueError):
                raise BackendError('音声サービスに古い状態が残っています。復旧が必要です') from None
        if (run/'lock').exists():
            raise BackendError('音声サービスのロックを確認してください')
        with (STATE/'audio-start.log').open('ab') as log:
            os.fchmod(log.fileno(),0o600)
            process=subprocess.Popen(['/bin/sh','/usr/local/sbin/jtb1-soloist','run'],stdin=subprocess.DEVNULL,
                                     stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+18
        while time.monotonic()<deadline:
            if process.poll() is not None:raise BackendError('音声サービスを開始できませんでした')
            try:
                if (run/'router-state').read_text().strip()=='watching' and (run/'soloist.pid').exists():
                    ctl('now');return
            except (OSError,BackendError):pass
            time.sleep(0.2)
        raise BackendError('音声サービスの開始待ちです。もう一度再生してください')
    finally:os.close(lock)


def load_volume(path=None):
    path = Path(path) if path is not None else STATE/'audio-preferences.json'
    try:
        saved = json.loads(path.read_text())
        value = saved.get('volume') if isinstance(saved, dict) else None
        return value if type(value) is int and 0 <= value <= 100 else None
    except (OSError, ValueError):
        return None


def save_volume(value, path=None):
    if type(value) is not int or not 0 <= value <= 100:
        raise BackendError('保存する音量が不正です')
    path = Path(path) if path is not None else STATE/'audio-preferences.json'
    if load_volume(path) != value:
        atomic_json(path, {'volume': value})


class Player:
    def __init__(self, run=Path('/run/jtb1-soloist'), jack=Path('/sys/class/switch/h2w/state'), caller=ctl, starter=None,
                 preferences=None):
        self.run, self.jack, self.caller = Path(run), Path(jack), caller
        self.preferences = Path(preferences) if preferences is not None else STATE/'audio-preferences.json'
        self.starter = starter
        self.lock = threading.Lock()

    def stable_route(self):
        def route(raw):
            if raw in ('1','2'): return 'headphones'
            if raw=='0': return 'speaker'
            raise BackendError('イヤホンの状態を確認してください')
        try:
            for name in ('router','soloist','pulse'):
                os.kill(int((self.run/(name+'.pid')).read_text()),0)
            if (self.run/'router-state').read_text().strip()!='watching':
                raise BackendError('音声経路の監視が停止しています')
            current = route(self.jack.read_text().strip())
            if current != route((self.run/'jack-state').read_text().strip()) or current != (self.run/'output').read_text().strip():
                raise BackendError('音声の出力先を切替中です。再度操作してください')
            return current
        except (OSError,ValueError):
            raise BackendError('音声サービスの状態を確認してください') from None

    def volume_limit(self):
        try:
            ready=(self.run/'volume-lock-ready').read_text().strip()
            if self.stable_route() in ('speaker','headphones') and ready==(self.run/'router.pid').read_text().strip():return 100
        except (OSError,BackendError,ValueError):pass
        return 0

    @contextmanager
    def volume_guard(self):
        try:fd=os.open(self.run/'volume-route.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        except OSError:raise BackendError('音声サービスの状態を確認してください') from None
        try:
            deadline=time.monotonic()+8
            while True:
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise BackendError('音声切替中です。もう一度操作してください')
                    time.sleep(0.05)
            # The gated shell RPC inherits this descriptor. If the GUI dies,
            # routing still waits until its outstanding volume writer exits.
            VOLUME_LOCK.fd=fd
            yield
        finally:
            VOLUME_LOCK.fd=None
            os.close(fd)

    def set_volume(self, value):
        if type(value) is not int or not 0<=value<=100:raise BackendError('音量は0〜100%で指定してください')
        with self.volume_guard():
            before=self.stable_route()
            if self.volume_limit()!=100:
                raise BackendError('音声監視の準備が終わるまで音量を変更できません')
            self.caller('volume',value)
            try:
                if self.stable_route()!=before:raise BackendError('音声の出力先が変わりました')
            except BackendError:
                self.caller('pause')
                raise BackendError('挿抜を検出し、一時停止しました') from None
            try:
                save_volume(value, self.preferences)
            except OSError:
                raise BackendError('音量を変更しましたが保存できませんでした') from None

    def action(self, command, value=None):
        with self.lock, control_lease(command):
            if command=='pause':
                return self.caller('pause')
            if command=='volume':
                return self.set_volume(value)
            if command not in ('play','next','prev','seek','shuffle','repeat'):
                raise BackendError('未対応の操作です')
            if command=='play' and value is not None and not URI.fullmatch(str(value)):
                raise BackendError('再生URIが不正です')
            if command=='seek' and (type(value) is not int or not 0<=value<=86400000):
                raise BackendError('再生位置が不正です')
            if command=='shuffle' and value not in ('on','off'):
                raise BackendError('シャッフル設定が不正です')
            if command=='repeat' and value not in ('off','context','track'):
                raise BackendError('リピート設定が不正です')
            if command=='play' and self.starter and not (self.run/'runner.pid').exists():
                self.starter()
            before = self.stable_route()
            if command=='play' and not self.caller('now').get('is_active'):
                self.caller('activate')
            self.caller(command,value) if value is not None else self.caller(command)
            if command in ('play','next','prev','seek'):
                time.sleep(0.3)
                try:
                    after = self.stable_route()
                except BackendError:
                    self.caller('pause')
                    raise
                if after != before:
                    self.caller('pause')
                    raise BackendError('挿抜を検出したため一時停止しました')
