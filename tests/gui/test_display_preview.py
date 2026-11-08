import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import display_preview as gui
from portrait_geometry import image_to_panel, portrait_to_panel

FONT = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
CATALOG = {key: [{'title': '日本語の表示テスト', 'subtitle': 'アーティスト'}] * 5
           for key in ('search', 'saved', 'playlists')}


class TouchTests(unittest.TestCase):
    def frame(self, t, *events):
        for code, value in events:
            self.assertIsNone(t.feed(3, code, value))
        return t.feed(0, 0, 0)

    def test_tap_fires_only_on_release(self):
        t = gui.Touch()
        self.assertIsNone(self.frame(t, (57, 7), (53, 400), (54, 250)))
        self.assertEqual(self.frame(t, (57, -1)), (400, 250))
        self.assertIsNone(self.frame(t))

    def test_ignore_non_primary_slot(self):
        t = gui.Touch()
        self.frame(t, (47, 1), (57, 10), (53, 700), (54, 200))
        self.assertFalse(t.down)
        self.assertIsNone(self.frame(t, (57, -1)))

    def test_drag_out_and_back_is_not_a_tap(self):
        t = gui.Touch()
        self.frame(t, (57, 1), (53, 100), (54, 100))
        self.frame(t, (53, 300))
        self.frame(t, (53, 100))
        self.assertIsNone(self.frame(t, (57, -1)))

    def test_dropped_frame_cancels_current_touch(self):
        t = gui.Touch()
        self.frame(t, (57, 1), (53, 100), (54, 100))
        t.feed(0, 3, 0)
        self.frame(t, (57, -1))
        self.assertIsNone(self.frame(t))
        self.assertIsNone(self.frame(t, (57, -1)))
        self.frame(t, (57, 2), (53, 800), (54, 400))
        self.assertEqual(self.frame(t, (57, -1)), (800, 400))


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.view = gui.View(FONT, CATALOG)

    def test_every_page_is_opaque_portrait_and_panel_is_correct_size(self):
        for page in ('now', 'search', 'saved', 'playlists'):
            self.view.page = page
            image = self.view.render()
            self.assertEqual(image.size, (600, 1024))
            self.assertEqual(image.getchannel('A').getextrema(), (255, 255))
            for orientation in ('cw', 'ccw'):
                data = image_to_panel(image, orientation).tobytes('raw', 'BGRA')
                self.assertEqual(len(data), 1024 * 600 * 4)

    def test_tabs_flip_and_exit(self):
        self.assertTrue(self.view.tap(200, 980))
        self.assertEqual(self.view.page, 'search')
        self.assertTrue(self.view.tap(450, 20))
        self.assertEqual(self.view.orientation, 'ccw')
        self.assertFalse(self.view.tap(550, 20))

    def test_display_and_touch_targets_match_both_rotations(self):
        for rotation in ('cw', 'ccw'):
            for point in ((40, 980), (190, 980), (350, 980), (530, 980), (430, 30)):
                panel = portrait_to_panel(*point, rotation)
                self.assertEqual(gui.panel_to_portrait(*panel, rotation), point)

    def test_long_text_clipped_by_measured_width(self):
        result = self.view.fitted('日本語' * 100, 28, 300)
        self.assertTrue(result.endswith('…'))
        self.assertLessEqual(self.view.fonts[28].getlength(result), 300)

    def test_control_characters_removed(self):
        self.assertEqual(gui.text('あ\nい\x00う'), 'あいう')

    def test_private_save_is_mode_600_and_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'saved'
            gui.save_private(path, b'original')
            gui.save_private(path, b'new')
            self.assertEqual(path.read_bytes(), b'new')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_player_worker_has_only_fixed_read_command(self):
        import ast
        tree = ast.parse(Path(gui.__file__).read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == 'Popen']
        self.assertEqual(len(calls), 1)
        self.assertEqual(ast.literal_eval(calls[0].args[0]),
                         ['/usr/local/sbin/jtb1-soloist', 'ctl', 'now', '--json'])


if __name__ == '__main__':
    unittest.main()
