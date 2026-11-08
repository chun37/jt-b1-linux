import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).parent / 'app'))
import physical_buttons as b

class ShortPressTests(unittest.TestCase):
    def setUp(self):
        self.d = b.ShortPress(b.MAPPING['omap4-keypad'])
    def press(self, code, duration=.1, start=10):
        self.assertIsNone(self.d.feed(1, code, 1, start))
        return self.d.feed(1, code, 0, start + duration)
    def test_a1(self):self.assertEqual(self.press(158), 'prev')
    def test_a2(self):self.assertEqual(self.press(102), 'toggle')
    def test_a3(self):self.assertEqual(self.press(139), 'next')
    def test_power(self):
        self.d = b.ShortPress(b.MAPPING['twl4030_pwrbutton'])
        self.assertEqual(self.press(116), 'home')
    def test_long(self):self.assertIsNone(self.press(102, 1))
    def test_repeat(self):
        self.d.feed(1,102,1,10)
        self.assertIsNone(self.d.feed(1,102,2,10.1))
        self.assertEqual(self.d.feed(1,102,0,10.2),'toggle')
        self.assertIsNone(self.d.feed(1,102,0,10.3))
    def test_orphan_release(self):self.assertIsNone(self.d.feed(1,102,0,10))
    def test_duplicate_down(self):
        self.d.feed(1,102,1,10)
        self.d.feed(1,102,1,11)
        self.assertIsNone(self.d.feed(1,102,0,11.1))
    def test_chord(self):
        self.d.feed(1,102,1,10)
        self.d.feed(1,139,1,10.1)
        self.assertIsNone(self.d.feed(1,102,0,10.2))
        self.assertIsNone(self.d.feed(1,139,0,10.3))
    def test_dropped(self):
        self.d.feed(1,102,1,10)
        self.d.feed(0,3,0,10.1)
        self.assertIsNone(self.d.feed(1,102,0,10.2))
        self.d.feed(0,0,0,10.3)
        self.assertIsNone(self.d.feed(1,102,0,10.4))
        self.assertEqual(self.press(102,start=11),'toggle')
    def test_unknown(self):self.assertIsNone(self.press(30))
    def test_non_key(self):self.assertIsNone(self.d.feed(4,102,1,10))
    def test_debounce(self):
        self.assertEqual(self.press(102,duration=.01),'toggle')
        self.assertIsNone(self.press(102,duration=.01,start=10.05))
    def test_negative_time(self):self.assertIsNone(self.press(102,duration=-1))

class ActionTests(unittest.TestCase):
    def test_toggle(self):
        for status, action in [('playing','pause'),('paused','play'),('stopped','play')]:
            player = Mock()
            b.perform('toggle',player,lambda cmd: {'status':status})
            player.action.assert_called_once_with(action)
    def test_unknown_state_fails_closed(self):
        player = Mock()
        with self.assertRaises(ValueError):b.perform('toggle',player,lambda cmd:{})
        player.action.assert_not_called()
    def test_prev_next(self):
        for action in ('prev','next'):
            player=Mock();b.perform(action,player)
            player.action.assert_called_once_with(action)
    def test_disallow(self):
        with self.assertRaises(ValueError):b.perform('home',Mock())
    def test_worker_timeout_reaps_leases(self):
        obj=b.Buttons.__new__(b.Buttons)
        obj.worker=Mock(pid=123,returncode=-15)
        obj.worker.poll.side_effect=[None,-15]
        obj.started=0;obj.status=Mock()
        with patch.object(b.time,'monotonic',return_value=30),patch.object(b,'cancel_orphan_controls') as clean:
            obj.reap()
        clean.assert_called_once_with(123)
        self.assertIsNone(obj.worker)

if __name__ == '__main__':unittest.main()
