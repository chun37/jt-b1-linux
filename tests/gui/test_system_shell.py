import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageFont
import system_shell as shell
import system_status as status
from portrait_geometry import image_to_panel, panel_to_portrait


class Geometry(unittest.TestCase):
    def test_bar_not_app_input(self):
        for y in (0, 20, 43, -1, 1024):
            self.assertIsNone(shell.app_point(20, y, True))
        for x in (-1, 600):
            self.assertIsNone(shell.app_point(x, 100))

    def test_native_bounds(self):
        self.assertEqual(shell.app_point(0, 44), (0, 0))
        self.assertEqual(shell.app_point(599, 1023), (599, 979))

    def test_legacy_roundtrip(self):
        for y in range(1024):
            screen_y = 44 + int(y * 980 / 1024)
            restored = shell.app_point(300, screen_y, True)[1]
            self.assertLessEqual(abs(restored - y), 2)

    def test_fixed_rotation(self):
        image = Image.new('RGBA', (600, 1024), 'black')
        image.putpixel((10, 20), (255, 0, 0, 255))
        panel = image_to_panel(image, shell.ORIENTATION)
        for y in range(panel.height):
            for x in range(panel.width):
                if panel.getpixel((x, y)) == (255, 0, 0, 255):
                    self.assertEqual(panel_to_portrait(x, y, shell.ORIENTATION), (10, 20))
                    return
        self.fail('pixel missing')


class Protocol(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(shell, 'ROOT', Path(self.temp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_publish_and_close(self):
        app = shell.AppSurface('test')
        self.assertTrue(shell.alive(shell.load(shell.ROOT / 'app.json')))
        app.present(Image.new('RGB', (600, 980), 'red'))
        self.assertEqual((shell.ROOT / 'frame.rgba').stat().st_size, 600 * 980 * 4)
        self.assertEqual((shell.ROOT / 'frame.rgba').stat().st_mode & 0o777, 0o600)
        app.close()
        self.assertFalse((shell.ROOT / 'app.json').exists())

    def test_wrong_viewport(self):
        app = shell.AppSurface()
        try:
            with self.assertRaises(ValueError):
                app.present(Image.new('RGBA', (600, 1024)))
        finally:
            app.close()

    def test_single_app(self):
        app = shell.AppSurface()
        try:
            with self.assertRaises(BlockingIOError):
                shell.AppSurface()
        finally:
            app.close()

    def test_stale_pid(self):
        self.assertFalse(shell.alive({'pid': os.getpid(), 'start': 'wrong'}))
        self.assertFalse(shell.alive({}))

    def test_new_app_retires_old_frame(self):
        first = shell.AppSurface()
        first.present(Image.new('RGBA', (600, 980)))
        first.close()
        second = shell.AppSurface()
        self.assertFalse((shell.ROOT / 'frame.rgba').exists())
        second.close()


class ClientInput(unittest.TestCase):
    def test_statusbar_is_ignored_and_bottom_tab_maps_to_legacy(self):
        from portrait_geometry import portrait_to_panel
        import display_preview as surface
        class View:
            def __init__(self):
                self.page, self.marker, self.state = 'now', None, {}
                self.taps = []
            def tap(self, x, y):
                self.taps.append((x, y))
                return False
            def render(self):
                return Image.new('RGBA', (600, 1024), '#123456')
        view = View()
        reader, writer = os.pipe()
        events = []
        for screen_x, screen_y in ((220, 20), (220, 980)):
            x, y = portrait_to_panel(screen_x, screen_y, 'ccw')
            events.extend([(3,57,4), (3,53,x), (3,54,y), (0,0,0), (3,57,-1), (0,0,0)])
        os.write(writer, b''.join(surface.EVENT.pack(0, 0, *event) for event in events))
        os.close(writer)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'catalog.json').write_text('{}')
            with patch.object(shell, 'ROOT', root / 'shell'), patch.object(surface, 'RUN', root / 'gui'), patch.object(shell, 'ensure'), patch.object(surface, 'open_touch', return_value=(reader, 0, 0)), patch.object(surface, 'playback_worker', return_value=None):
                shell.run_client(None, root / 'catalog.json', 1, 'test', lambda *args: view)
            self.assertEqual(view.taps, [(220, int((980 - 44) * 1024 / 980))])
            self.assertEqual(view.orientation, 'ccw')


class Indicators(unittest.TestCase):
    def test_route_lookup_selects_wifi(self):
        from subprocess import CompletedProcess
        with patch.object(status.subprocess, 'run', return_value=CompletedProcess([], 0, '1.1.1.1 via 192.0.2.1 dev wlan0 src 192.0.2.2', '')):
            self.assertEqual(status.active_interface(), 'wlan0')

    def test_network_display_is_exclusive(self):
        state = {'active_interface': 'wlan0', 'wifi': {'connected': True}, 'cell': {'registered': True, 'rat': 'LTE', 'data_connected': True}}
        self.assertEqual(status.network_label(state), 'Wi-Fi')
        state['active_interface'] = 'usb0'
        self.assertEqual(status.network_label(state), 'LTE')
        self.assertEqual(status.network_label(state, stale=True), '回線不明')
        state['active_interface'] = ''
        self.assertEqual(status.network_label(state), 'オフライン')

    def test_no_route(self):
        from subprocess import CompletedProcess
        with patch.object(status.subprocess, 'run', return_value=CompletedProcess([], 2, '', 'Network unreachable')):
            self.assertEqual(status.active_interface(), '')

    def test_percentage_is_unsigned_after_icon_and_no_connection_caption(self):
        from PIL import ImageDraw
        font = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
        fonts = {size: ImageFont.truetype(font, size) for size in (18, 22)}
        state = {'updated_at': time.time(), 'active_interface': 'wlan0', 'wifi': {'connected': True, 'bars': 3}, 'cell': {'rat': 'LTE', 'registered': True}, 'battery': 95, 'charging': True}
        with patch.object(ImageDraw.ImageDraw, 'text') as text, patch.object(ImageDraw.ImageDraw, 'rounded_rectangle') as icon:
            status.render_bar(fonts, state)
            labels = [call.args[1] for call in text.call_args_list]
            self.assertIn('Wi-Fi', labels)
            self.assertNotIn('LTE', labels)
            self.assertNotIn('接続中', labels)
            self.assertIn('95%', labels)
            self.assertNotIn('+95%', labels)
            percentage_call = next(call for call in text.call_args_list if call.args[1] == '95%')
            self.assertGreater(percentage_call.args[0][0], icon.call_args.args[0][2])
        state['active_interface'] = 'usb0'
        with patch.object(ImageDraw.ImageDraw, 'text') as text:
            status.render_bar(fonts, state)
            labels = [call.args[1] for call in text.call_args_list]
            self.assertIn('LTE', labels)
            self.assertNotIn('Wi-Fi', labels)

    def test_lte(self):
        self.assertEqual(status.parse_serving({1: bytes([1, 1, 1, 2, 1, 8])}), {'registered': True, 'rat': 'LTE'})

    def test_no_service(self):
        self.assertEqual(status.parse_serving({1: bytes([0, 0, 0, 0, 0])})['rat'], '圏外')

    def test_umts(self):
        self.assertEqual(status.parse_serving({1: bytes([1, 1, 1, 2, 1, 5])})['rat'], '3G')

    def test_unknown_is_not_lte(self):
        self.assertEqual(status.parse_serving({1: bytes([1, 1, 1, 2, 1, 99])})['rat'], '不明')

    def test_truncated(self):
        for raw in (b'', bytes(4), bytes([1, 1, 1, 2, 2, 8])):
            with self.assertRaises(ValueError):
                status.parse_serving({1: raw})

    def test_wifi_does_not_expose_identifiers(self):
        from subprocess import CompletedProcess
        with patch.object(status.subprocess, 'run', return_value=CompletedProcess([], 0, 'ssid=private\nbssid=private\nwpa_state=COMPLETED\n', '')), patch.object(status, 'read', return_value=' wlan0: 0000 44. -66. -256 0'):
            self.assertEqual(status.wifi_status(), {'connected': True, 'bars': 3})

    def test_wifi_disconnected(self):
        from subprocess import CompletedProcess
        with patch.object(status.subprocess, 'run', return_value=CompletedProcess([], 0, 'wpa_state=SCANNING\n', '')), patch.object(status, 'read', return_value=''):
            self.assertEqual(status.wifi_status(), {'connected': False, 'bars': 0})

    def test_render_unknown_connected_stale(self):
        font = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
        fonts = {size: ImageFont.truetype(font, size) for size in (18, 22)}
        for state in ({}, {'updated_at': time.time(), 'wifi': {'connected': True, 'bars': 4}, 'cell': {'rat': 'LTE', 'registered': True, 'data_connected': True}, 'battery': 95, 'charging': True}, {'updated_at': 1, 'battery': None}):
            self.assertEqual(status.render_bar(fonts, state).size, (600, 44))


if __name__ == '__main__':
    unittest.main()
