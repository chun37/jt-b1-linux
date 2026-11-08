import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import spotify_backend as backend
from auth_helper import atomic_json
import gui_service
import display_preview as surface


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=patch.object(backend,'RPC',self.root);self.patch.start();self.addCleanup(self.patch.stop)
    def tearDown(self):self.temp.cleanup()
    def gate(self):
        process=subprocess.Popen(['/bin/sh','-c','read ready'],stdin=subprocess.PIPE,start_new_session=True)
        def cleanup():
            if process.poll() is None:process.kill()
            process.communicate()
        self.addCleanup(cleanup)
        return process
    def test_orphan_gate_killed_before_fail_closed_pause(self):
        process=self.gate();owner=999999
        atomic_json(self.root/('rpc-'+str(process.pid)+'.json'),{'owner':owner,'pid':process.pid,'start':backend.process_start(process.pid)})
        atomic_json(self.root/('action-'+str(owner)+'.json'),{'owner':owner,'command':'play'})
        with patch.object(backend,'ctl') as control:
            self.assertTrue(backend.cancel_orphan_controls(owner));control.assert_called_once_with('pause')
        self.assertEqual(process.wait(timeout=2),-signal.SIGKILL)
        self.assertFalse(list(self.root.glob('*.json')))
    def test_pid_reuse_never_kills_current_process(self):
        process=self.gate();owner=999999
        atomic_json(self.root/'rpc.json',{})
        atomic_json(self.root/('rpc-'+str(process.pid)+'.json'),{'owner':owner,'pid':process.pid,'start':'different-start-time'})
        with patch.object(backend,'ctl') as control:
            self.assertFalse(backend.cancel_orphan_controls(owner));control.assert_not_called()
        self.assertIsNone(process.poll())
    def test_control_lease_removed_after_exception(self):
        with self.assertRaises(ValueError):
            with backend.control_lease('play'):
                self.assertTrue((self.root/('action-'+str(os.getpid())+'.json')).exists())
                raise ValueError('synthetic')
        self.assertFalse(list(self.root.glob('action-*.json')))
    def test_unknown_cli_command_never_starts_process(self):
        with patch.object(backend.subprocess,'Popen') as popen:
            with self.assertRaises(backend.BackendError):backend.ctl('shell')
            popen.assert_not_called()


class HiddenWakeTests(unittest.TestCase):
    def test_real_touch_decoder_reopens_on_one_release(self):
        reader,writer=os.pipe()
        events=[(3,0x39,5),(3,0x35,739),(3,0x36,150),(0,0,0),(3,0x39,-1),(0,0,0)]
        os.write(writer,b''.join(surface.EVENT.pack(0,0,*event) for event in events));os.close(writer)
        with patch.object(surface,'open_touch',return_value=(reader,0,0)), patch.object(gui_service.system_shell,'ensure'):
            gui_service.wait_touch([False])
        with self.assertRaises(OSError):os.fstat(reader)

if __name__=='__main__':unittest.main()
