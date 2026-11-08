import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import boot_playback as b

URI = 'spotify:track:15YQ8yAW1CBlFyCIIh45qx'
NOW = {'item': {'uri': URI}, 'status': 'paused', 'is_active': False, 'volume': 100}


class AutoplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root/'run'; self.run.mkdir()
        self.state = self.root/'state'; self.state.mkdir()
        self.caller = Mock(return_value=NOW)
        self.player = Mock()
        self.network = Mock()
        self.starter = Mock()
        self.time = 0
        self.cancel = False
        self.a = self.make()

    def make(self, boot='a'):
        return b.Autoplay(self.run, self.state, boot, self.caller,
                          self.starter, self.player, self.network, lambda: self.time,
                          lambda: self.cancel)

    def status(self):
        return b.read_json(self.a.marker).get('status')

    def test_existing_track_resumes_without_volume_change(self):
        self.a.tick()
        self.player.action.assert_called_once_with('play', None)
        self.assertEqual(self.status(), 'play-requested')
        self.assertEqual(NOW['volume'], 100)

    def test_saved_volume_is_restored_before_play(self):
        b.save_volume(30,self.a.preferences)
        self.a.tick()
        self.assertEqual(self.player.action.call_args_list,
                         [unittest.mock.call('volume',30), unittest.mock.call('play',None)])

    def test_saved_mute_is_restored_before_play(self):
        b.save_volume(0,self.a.preferences)
        self.a.tick()
        self.assertEqual(self.player.action.call_args_list[0],unittest.mock.call('volume',0))

    def test_volume_restore_failure_never_starts_playback(self):
        b.save_volume(30,self.a.preferences)
        self.player.action.side_effect=b.BackendError('route unavailable')
        self.a.tick();self.make().tick()
        self.player.action.assert_called_once_with('volume',30)
        self.assertEqual(self.status(),'play-failed')

    def test_bad_volume_uses_existing_default_without_setting_volume(self):
        self.a.preferences.write_text('{"volume":101}')
        self.a.tick()
        self.player.action.assert_called_once_with('play',None)

    def test_already_playing_never_resets_to_saved_volume(self):
        b.save_volume(30,self.a.preferences)
        self.caller.return_value=dict(NOW,status='playing')
        self.a.tick()
        self.player.action.assert_not_called()

    def test_cancel_during_volume_restore_never_starts_playback(self):
        b.save_volume(30,self.a.preferences)
        self.player.action.side_effect=lambda *args:setattr(self,'cancel',True)
        self.a.tick()
        self.player.action.assert_called_once_with('volume',30)
        self.assertEqual(self.status(),'cancelled-before-play')

    def test_cold_start_uses_saved_track(self):
        b.remember(NOW, self.a.saved)
        self.caller.return_value = {}
        self.a.tick()
        self.player.action.assert_called_once_with('play', URI)

    def test_no_saved_track_never_plays(self):
        self.caller.return_value = {}
        self.a.tick()
        self.player.action.assert_not_called()
        self.assertEqual(self.status(), 'no-saved-track')

    def test_already_playing_not_restarted(self):
        self.caller.return_value = dict(NOW, status='playing')
        self.a.tick()
        self.player.action.assert_not_called()
        self.assertEqual(self.status(), 'already-playing')

    def test_same_boot_new_worker_never_replays(self):
        self.a.tick(); self.make().tick()
        self.player.action.assert_called_once()

    def test_new_boot_plays_again(self):
        self.a.tick(); self.make('b').tick()
        self.assertEqual(self.player.action.call_count, 2)

    def test_network_failure_retry_before_play_only(self):
        self.network.side_effect = b.BackendError('offline')
        self.a.tick()
        self.starter.assert_not_called()
        self.assertFalse(self.a.finished)
        self.network.side_effect = None
        self.a.tick()
        self.player.action.assert_called_once()

    def test_start_failure_does_not_play(self):
        self.starter.side_effect = b.BackendError('not ready')
        self.a.tick()
        self.player.action.assert_not_called()
        self.assertFalse(self.a.finished)

    def test_route_failure_does_not_play(self):
        self.player.stable_route.side_effect = b.BackendError('unplug')
        self.a.tick()
        self.player.action.assert_not_called()
        self.assertFalse(self.a.finished)

    def test_timeout_stops_even_after_worker_restart(self):
        self.time = 180
        self.a.tick(); self.make().tick()
        self.network.assert_not_called()
        self.assertEqual(self.status(), 'readiness-timeout')

    def test_play_failure_is_not_retried(self):
        self.player.action.side_effect = b.BackendError('ambiguous')
        self.a.tick(); self.a.tick(); self.make().tick()
        self.player.action.assert_called_once()
        self.assertEqual(self.status(), 'play-failed')

    def test_claim_precedes_play(self):
        def action(*args):
            self.assertEqual(self.status(), 'claimed')
            self.make().tick()
        self.player.action.side_effect = action
        self.a.tick()
        self.player.action.assert_called_once()

    def test_cancel_after_readiness_never_plays(self):
        self.starter.side_effect = lambda: setattr(self, 'cancel', True)
        self.a.tick()
        self.player.action.assert_not_called()
        self.assertFalse(self.a.marker.exists())

    def test_deployment_marker_suppresses_current_boot(self):
        self.a.mark('deployment-skipped')
        self.make().tick()
        self.network.assert_not_called()
        self.player.action.assert_not_called()

    def test_empty_snapshot_does_not_erase_last_track(self):
        b.remember(NOW, self.a.saved)
        b.remember({}, self.a.saved)
        self.assertEqual(b.read_json(self.a.saved), {'uri': URI})

    def test_unchanged_snapshot_does_not_write_flash(self):
        b.remember(NOW, self.a.saved)
        before = self.a.saved.stat().st_mtime_ns
        b.remember(NOW, self.a.saved)
        self.assertEqual(before, self.a.saved.stat().st_mtime_ns)

    def test_only_track_or_episode_recorded(self):
        for uri in ['spotify:playlist:'+'a'*22, 'bogus', None]:
            self.assertIsNone(b.track_record({'item': {'uri': uri}}))
        self.assertEqual(b.track_record({'item': {'uri': 'spotify:episode:'+'a'*22}}),
                         {'uri': 'spotify:episode:'+'a'*22})

    def test_bad_json_is_not_executed(self):
        self.a.saved.write_text('not json')
        self.caller.return_value = {}
        self.a.tick()
        self.player.action.assert_not_called()

    def test_no_credential_or_volume_in_saved_record(self):
        b.remember(dict(NOW, access_token='synthetic'), self.a.saved)
        self.assertEqual(json.loads(self.a.saved.read_text()), {'uri': URI})
        self.assertEqual(self.a.saved.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
