import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

BASE = Path(__file__).resolve().parent
SCRIPT = BASE.parents[1] / 'display/jtb1-battery-led'

class BatteryLedTests(unittest.TestCase):
    def policy(self, status, health, level):
        return tuple(map(int, subprocess.check_output(
            ['sh', str(SCRIPT), '--policy', status, health, str(level)], text=True).split()))

    def test_full_green(self):
        self.assertEqual(self.policy('Full', 'Good', 100), (0, 255, 0, 0, 0, 0xff00))

    def test_charging_stock_amber(self):
        for level in (0, 14, 15, 99, 100):
            self.assertEqual(self.policy('Charging', 'Good', level), (154, 154, 0, 0, 0, 0x9a9a00))

    def test_noncharging_off_at_normal_capacity(self):
        for status in ('Not charging', 'Discharging', 'Unknown'):
            self.assertEqual(self.policy(status, 'Good', 50), (0, 0, 0, 0, 0, 0))

    def test_low_noncharging_red_steady(self):
        self.assertEqual(self.policy('Discharging', 'Good', 14), (255, 0, 0, 0, 0, 0xff0000))
        self.assertEqual(self.policy('Discharging', 'Good', 15), (0, 0, 0, 0, 0, 0))

    def test_overheat_stock_amber_blink_takes_priority(self):
        for status in ('Charging', 'Full', 'Discharging'):
            self.assertEqual(self.policy(status, 'Overheat', 5), (154, 154, 0, 125, 2875, 0x9a9a00))



    def test_daemon_repairs_component_drift_without_battery_state_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values = {'power_supply/smb137c/status': 'Full',
                      'power_supply/smb137c/health': 'Good',
                      'power_supply/battery/capacity': '100',
                      'leds/red/brightness': '0', 'leds/green/brightness': '0',
                      'leds/blue/brightness': '0', 'leds/red/delay_on': '0',
                      'leds/red/delay_off': '0', 'leds/color/brightness': '0'}
            for name, value in values.items():
                path = root / 'sys/class' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value + '\n')
            (root / 'run').mkdir()
            (root / 'run/jtb1-display-ready').write_text('ready\n')
            text = SCRIPT.read_text().replace('/sys/class/', str(root / 'sys/class') + '/')
            text = text.replace('/run/', str(root / 'run') + '/').replace('sleep 2', 'sleep 0.05')
            copied = root / 'daemon.sh'
            copied.write_text(text)
            green = root / 'sys/class/leds/green/brightness'
            def wait_green():
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if green.read_text().strip() == '255':
                        return
                    time.sleep(0.02)
                self.fail('daemon did not restore green')
            proc = subprocess.Popen(['sh', str(copied)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                wait_green()
                green.write_text('0\n')
                wait_green()
                # Simulate another later kernel reset, with Full/Good/100 unchanged.
                green.write_text('0\n')
                wait_green()
            finally:
                proc.terminate()
                proc.wait(timeout=3)

    def test_no_raw_or_trigger_or_charger_writes(self):
        text = SCRIPT.read_text()
        for path in ('/dev/mmc', '/dev/block', '/dev/i2c', '/dev/mem'):
            self.assertNotIn(path, text)
        self.assertNotIn('/trigger', text)
        self.assertNotIn('> /sys/class/power_supply', text)
        subprocess.run(['sh', '-n', str(SCRIPT)], check=True)

if __name__ == '__main__':
    unittest.main()
