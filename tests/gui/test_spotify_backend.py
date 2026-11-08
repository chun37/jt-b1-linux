import io
import json
import os
import tempfile
import time
import fcntl
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from auth_helper import atomic_json, validated_token
from pkce_core import SCOPES
import spotify_backend as b
from japanese_input import Keyboard, katakana


class Reply:
    status=200
    def __init__(self,payload):self.payload=json.dumps(payload).encode()
    def read(self,n):return self.payload[:n]
    def __enter__(self):return self
    def __exit__(self,*a):pass


class HTTP:
    def __init__(self,*responses):self.responses=list(responses);self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        response=self.responses.pop(0)
        if isinstance(response,Exception):raise response
        return Reply(response)


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.token=validated_token({'access_token':'synthetic-access','refresh_token':'synthetic-refresh',
                                   'token_type':'Bearer','expires_in':3600,'scope':' '.join(SCOPES)},b.CLIENT_ID)
        atomic_json(self.directory/'web-api-token.json',self.token)
    def tearDown(self):self.temp.cleanup()

    def test_refresh_preserves_omitted_refresh_token_and_scope(self):
        self.token['expires_at']=0;atomic_json(self.directory/'web-api-token.json',self.token)
        http=HTTP({'access_token':'new-synthetic','token_type':'Bearer','expires_in':3600})
        client=b.WebAPI(self.directory,http)
        self.assertEqual(client.token(),'new-synthetic')
        saved=b.read_credential(client.credential)
        self.assertEqual(saved['refresh_token'],'synthetic-refresh')
        self.assertEqual(client.credential.stat().st_mode&0o777,0o600)
        self.assertNotIn('Authorization',http.requests[0].headers)
        self.assertIn(b'grant_type=refresh_token',http.requests[0].data)

    def test_invalid_refresh_never_overwrites_valid_file(self):
        original=(self.directory/'web-api-token.json').read_bytes()
        client=b.WebAPI(self.directory,HTTP({'access_token':'bad\ncredential','expires_in':3600,'token_type':'Bearer'}))
        with self.assertRaises(b.BackendError):client.token(force=True)
        self.assertEqual((self.directory/'web-api-token.json').read_bytes(),original)

    def test_401_refreshes_once_and_retries_read_only_get(self):
        error=HTTPError(b.API+'/me',401,'unauthorized',{},io.BytesIO(b'not logged'))
        http=HTTP(error,{'access_token':'new','refresh_token':'new-refresh','expires_in':3600,'token_type':'Bearer'}, {'id':'example'})
        client=b.WebAPI(self.directory,http)
        self.assertEqual(client.get('/me'),{'id':'example'})
        self.assertEqual([r.get_method() for r in http.requests],['GET','POST','GET'])
        self.assertEqual(http.requests[-1].get_header('Authorization'),'Bearer new')

    def test_pages_accept_new_item_and_old_track_and_validate_next(self):
        uri='spotify:track:'+'a'*22
        raw={'items':[{'item':{'uri':uri,'name':'日本語'}},{'track':{'uri':uri,'name':'過去形式'}}],
             'next':b.API+'/playlists/'+'b'*22+'/items?offset=10&limit=10','total':12}
        client=b.WebAPI(self.directory,HTTP(raw))
        result=client.page('playlist',identifier='b'*22)
        self.assertEqual([x['title'] for x in result['rows']],['日本語','過去形式'])
        self.assertEqual(result['next_offset'],10)

    def test_block_untrusted_path_and_redirect(self):
        client=b.WebAPI(self.directory,HTTP())
        for path in ('https://evil.example/me','/me/../../tokens','/playlists/a/items?foo=bar'):
            with self.assertRaises(b.BackendError):client.get(path)
        with self.assertRaises(b.BackendError):b.NoRedirect().redirect_request(None,None,None,None,None,'https://evil.example')

    def test_permission_and_rate_limit_errors_are_sanitized(self):
        http=HTTP(HTTPError(b.API+'/me',429,'secret should not appear',{'Retry-After':'50'},io.BytesIO(b'secret')))
        client=b.WebAPI(self.directory,http)
        with self.assertRaises(b.BackendError) as error:client.get('/me')
        self.assertNotIn('secret',str(error.exception))
        with self.assertRaises(b.BackendError):client.get('/me')
        self.assertEqual(len(http.requests),1)

    def test_private_file_symlinks_and_broad_permissions_rejected(self):
        path=self.directory/'web-api-token.json';path.chmod(0o644)
        with self.assertRaises(b.BackendError):b.read_credential(path)
        path.unlink();path.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):b.read_credential(path)


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.run=Path(self.temp.name)
        self.rpc_patch=patch.object(b,'RPC',self.run/'rpc');self.rpc_patch.start()
        self.addCleanup(self.rpc_patch.stop)
        for name,value in {'router.pid':str(os.getpid()),'soloist.pid':str(os.getpid()),'pulse.pid':str(os.getpid()),
                           'router-state':'watching','jack-state':'2','output':'headphones','raw':'2','volume-lock-ready':str(os.getpid())}.items():
            (self.run/name).write_text(value)
        self.calls=[]
        def call(command,value=None):
            self.calls.append((command,value));return {'is_active':True} if command=='now' else None
        self.player=b.Player(self.run,self.run/'raw',call,preferences=self.run/'audio-preferences.json')
    def tearDown(self):self.temp.cleanup()
    def test_volume_is_persisted_before_return(self):
        self.player.action('volume',30)
        self.assertEqual(b.load_volume(self.player.preferences),30)
        self.assertEqual(self.player.preferences.stat().st_mode&0o777,0o600)

    def test_mute_is_persisted(self):
        self.player.action('volume',0)
        self.assertEqual(b.load_volume(self.player.preferences),0)

    def test_failed_route_transition_does_not_save_volume(self):
        def caller(command,value=None):
            if command=='volume':(self.run/'raw').write_text('0')
        self.player.caller=caller
        with self.assertRaises(b.BackendError):self.player.action('volume',30)
        self.assertIsNone(b.load_volume(self.player.preferences))

    def test_persistence_failure_is_reported(self):
        with patch.object(b,'save_volume',side_effect=OSError('synthetic')):
            with self.assertRaises(b.BackendError):self.player.action('volume',30)

    def test_preferences_accept_only_integer_percent(self):
        path=self.player.preferences
        for value in (-1,101,True,'30',None):
            path.write_text(json.dumps({'volume':value}))
            self.assertIsNone(b.load_volume(path))
        path.write_text('not json')
        self.assertIsNone(b.load_volume(path))

    def test_unchanged_volume_avoids_flash_write(self):
        self.player.action('volume',30)
        stamp=self.player.preferences.stat().st_mtime_ns
        self.player.action('volume',30)
        self.assertEqual(stamp,self.player.preferences.stat().st_mtime_ns)

    def test_route_transition_blocks_play(self):
        (self.run/'raw').write_text('0')
        with self.assertRaises(b.BackendError):self.player.action('play')
        self.assertEqual(self.calls,[])
    def test_pause_always_allowed(self):
        (self.run/'router-state').write_text('failed')
        self.player.action('pause');self.assertEqual(self.calls,[('pause',None)])
    def test_safe_volume_and_bad_arguments(self):
        for value in (-1,101,1000,'20',True):
            with self.assertRaises(b.BackendError):self.player.action('volume',value)
        self.player.action('volume',5)
        with self.assertRaises(b.BackendError):self.player.action('play','spotify:track:wrong;injection')
        with self.assertRaises(b.BackendError):self.player.action('seek',-1)
        self.assertEqual(self.calls,[('volume',5)])
    def test_speaker_supports_100_only_with_matching_watcher_lock_ready(self):
        (self.run/'raw').write_text('0');(self.run/'jack-state').write_text('0');(self.run/'output').write_text('speaker')
        (self.run/'volume-lock-ready').unlink()
        self.assertEqual(self.player.volume_limit(),0)
        with self.assertRaises(b.BackendError):self.player.action('volume',25)
        (self.run/'volume-lock-ready').write_text(str(os.getpid()))
        self.assertEqual(self.player.volume_limit(),100)
        self.player.action('volume',100)
        self.assertEqual(self.calls,[('volume',100)])

    def test_headphones_allow_full_requested_volume(self):
        def caller(command,value=None):
            self.calls.append((command,value));return {'volume':5} if command=='now' else None
        self.player.caller=caller
        self.assertEqual(self.player.volume_limit(),100)
        for value in (0,5,20,25,80,100):self.player.action('volume',value)
        self.assertEqual(self.calls,[('volume',v) for v in (0,5,20,25,80,100)])

    def test_jack_change_during_volume_is_paused_without_clamp(self):
        (self.run/'raw').write_text('0');(self.run/'jack-state').write_text('0');(self.run/'output').write_text('speaker')
        (self.run/'volume-lock-ready').write_text(str(os.getpid()))
        def caller(command,value=None):
            self.calls.append((command,value))
            if command=='volume' and value==100:(self.run/'raw').write_text('2')
        self.player.caller=caller
        with self.assertRaises(b.BackendError):self.player.action('volume',100)
        self.assertEqual(self.calls,[('volume',100),('pause',None)])

    def test_headphones_missing_or_stale_watcher_blocks_volume(self):
        for ready in ('missing','999999999'):
            (self.run/'volume-lock-ready').write_text(ready)
            self.assertEqual(self.player.volume_limit(),0)
            with self.assertRaises(b.BackendError):self.player.action('volume',100)
        self.assertEqual(self.calls,[])

    def test_volume_rpc_keeps_lock_context_and_clears_it_afterward(self):
        def caller(command,value=None):
            self.assertIsNotNone(getattr(b.VOLUME_LOCK,'fd',None))
            self.calls.append((command,value))
        self.player.caller=caller
        self.player.action('volume',5)
        self.assertIsNone(getattr(b.VOLUME_LOCK,'fd',None))

    def test_inherited_rpc_fd_holds_lock_after_gui_fd_closes(self):
        child=None;other=None
        try:
            with self.player.volume_guard():
                fd=b.VOLUME_LOCK.fd
                child=subprocess.Popen([sys.executable,'-I','-c','import os; os.read(0,1)'],stdin=subprocess.PIPE,pass_fds=(fd,))
            other=os.open(self.run/'volume-route.lock',os.O_RDWR)
            with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            child.communicate(input=b'G',timeout=3)
            fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:
            if child is not None and child.poll() is None:child.kill();child.communicate()
            if other is not None:os.close(other)

    def test_unplug_during_control_pauses(self):
        def change(command,value=None):
            self.calls.append((command,value))
            if command=='play':(self.run/'raw').write_text('0')
            return {'is_active':True} if command=='now' else None
        self.player.caller=change
        with patch.object(b.time,'sleep'):
            with self.assertRaises(b.BackendError):self.player.action('play')
        self.assertEqual(self.calls[-1],('pause',None))


class InputTests(unittest.TestCase):
    def test_japanese_and_ascii_composition(self):
        keyboard=Keyboard();keyboard.insert('はつね')
        self.assertIn('ハツネ',keyboard.candidates())
        keyboard.commit('初音');keyboard.insert('みく');keyboard.commit(katakana(keyboard.pending))
        self.assertEqual(keyboard.value,'初音ミク')
        keyboard.backspace();self.assertEqual(keyboard.value,'初音ミ')
    def test_bounded_query(self):
        keyboard=Keyboard()
        for _ in range(250):keyboard.insert('あ')
        self.assertEqual(len(keyboard.value),200)

if __name__=='__main__':unittest.main()
