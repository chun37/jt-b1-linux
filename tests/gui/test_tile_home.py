import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import standby_screen as home
import gui_service
import gui_control
import system_shell as shell
import system_status
from portrait_geometry import portrait_to_panel, image_to_panel


def panel(x, app_y):
    return portrait_to_panel(x, app_y + 44, 'ccw')


class TileHome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fonts = home.load_fonts('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
        # Synthetic fixture: no Spotify trademark asset is redistributed.
        cls.asset_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.asset_dir.cleanup)
        icon = Path(cls.asset_dir.name) / 'synthetic-icon.png'
        Image.new('RGBA', (939, 940), (30, 180, 80, 180)).save(icon)
        cls.icon_patch = patch.object(home, 'ICON', icon)
        cls.icon_patch.start()
        cls.addClassCleanup(cls.icon_patch.stop)

    def test_asset_compositing_pixels(self):
        image=home.render_body(self.fonts)
        with Image.open(home.ICON) as source:
            self.assertEqual(source.size,(939,940))
            icon=source.convert('RGBA')
        icon.thumbnail((76,76),Image.Resampling.LANCZOS)
        expected=Image.new('RGBA',icon.size,'#18372c')
        expected.alpha_composite(icon)
        x,y=home.SPOTIFY_TILE[0]+30,home.SPOTIFY_TILE[1]+34
        self.assertEqual(image.crop((x,y,x+icon.width,y+icon.height)).tobytes(),expected.tobytes())

    def test_missing_asset_keeps_labeled_tile(self):
        with patch.object(home,'ICON',Path('/nonexistent-spotify-image')):
            self.assertEqual(home.render_body(self.fonts).size,(600,980))
            self.assertEqual(home.launch_target(150,240),'spotify')

    def test_tile_interior(self):
        for p in ((24,132),(150,240),(287,395)):
            self.assertEqual(home.launch_target(*p), 'spotify')

    def test_tile_exterior(self):
        for p in ((23,132),(288,200),(100,131),(100,396),(400,300),(100,0),(100,900)):
            self.assertIsNone(home.launch_target(*p))

    def test_tile_tap(self):
        p = panel(150,240)
        self.assertEqual(gui_service.tile_action(p,p), 'spotify')

    def test_blank_tap(self):
        p = panel(450,600)
        self.assertIsNone(gui_service.tile_action(p,p))

    def test_bar_tap(self):
        p = portrait_to_panel(150,20,'ccw')
        self.assertIsNone(gui_service.tile_action(p,p))

    def test_drag_onto_tile(self):
        self.assertIsNone(gui_service.tile_action(panel(150,240),panel(400,240)))

    def test_drag_outside_tile(self):
        self.assertIsNone(gui_service.tile_action(panel(400,240),panel(150,240)))

    def test_no_or_invalid_touch(self):
        for p in (None,(-1,200),(1024,200)):
            self.assertIsNone(gui_service.tile_action(p,p))

    def test_boot_and_home_same_body(self):
        body = home.render_body(self.fonts)
        full = home.render_screen(self.fonts)
        self.assertEqual(body.size,(600,980))
        self.assertEqual(full.crop((0,44,600,1024)).tobytes(),body.tobytes())
        self.assertEqual(len(image_to_panel(full,'ccw').tobytes('raw','BGRA')),2457600)

    def test_tile_pixels_and_empty_grid_slot(self):
        image=home.render_body(self.fonts)
        self.assertNotEqual(image.getpixel((50,150)), image.getpixel((350,150)))
        self.assertEqual(image.getpixel((350,150)),image.getpixel((590,900)))

    def test_bar_unchanged(self):
        state={'updated_at':time.time(),'battery':95,'active_interface':'wlan0','wifi':{'bars':3}}
        full=home.render_screen(self.fonts,state,now=123456)
        self.assertEqual(full.crop((0,0,600,44)).tobytes(),
                         system_status.render_bar(self.fonts,state,now=123456).tobytes())

    def test_boot_no_network_probes(self):
        with patch.object(system_status,'collect',side_effect=AssertionError('network probe')):
            home.render_screen(self.fonts)

    def test_show_home_owns_current_manager_state(self):
        with tempfile.TemporaryDirectory() as d, patch.object(gui_service,'RUN',Path(d)), patch.object(gui_service,'wait_touch',return_value='spotify') as wait:
            stop=[False]
            gui_service.show_home(stop)
            state=json.loads((Path(d)/'service.json').read_text())
            self.assertEqual(state,{'state':'hidden','screen':'home','reopen':'tile','manager_pid':os.getpid()})
            wait.assert_called_once_with(stop)

    def test_partial_input_is_not_a_launch(self):
        import display_preview as surface
        reader,writer=os.pipe()
        os.write(writer,b'x');os.close(writer)
        with patch.object(surface,'open_touch',return_value=(reader,0,0)), patch.object(shell,'ensure'):
            with self.assertRaises(ValueError):gui_service.wait_touch([False])
        with self.assertRaises(OSError):os.fstat(reader)

    def test_home_retries_input_error_before_selection(self):
        with tempfile.TemporaryDirectory() as d, patch.object(gui_service,'RUN',Path(d)), patch.object(gui_service,'wait_touch',side_effect=[ValueError('partial'), 'spotify']) as wait, patch.object(gui_service.time,'sleep'):
            gui_service.show_home([False])
            self.assertEqual(wait.call_count,2)

    def test_home_readiness(self):
        record={'state':'hidden','screen':'home','manager_pid':123}
        status={'active':True,'app_active':False,'updated_at':time.time()}
        with patch.object(Path,'read_text',side_effect=[json.dumps(record),json.dumps(status)]):
            self.assertTrue(gui_control.home_ready(123))
        with patch.object(Path,'read_text',side_effect=[json.dumps(record),json.dumps(status)]):
            self.assertFalse(gui_control.home_ready(456))
        status['app_active']=True
        with patch.object(Path,'read_text',side_effect=[json.dumps(record),json.dumps(status)]):
            self.assertFalse(gui_control.home_ready(123))

    def test_blank_release_ignored_before_real_tile_release(self):
        import display_preview as surface
        reader,writer=os.pipe()
        events=[]
        for p in (panel(450,600), portrait_to_panel(150,20,'ccw'), panel(150,240)):
            events += [(3,0x39,5),(3,0x35,p[0]),(3,0x36,p[1]),(0,0,0),(3,0x39,-1),(0,0,0)]
        os.write(writer,b''.join(surface.EVENT.pack(0,0,*event) for event in events));os.close(writer)
        with patch.object(surface,'open_touch',return_value=(reader,0,0)), patch.object(shell,'ensure'), patch.object(gui_service,'tile_action',wraps=gui_service.tile_action) as action:
            gui_service.wait_touch([False])
            self.assertEqual(sum(call.args[0] is not None for call in action.call_args_list),3)
        with self.assertRaises(OSError):os.fstat(reader)


if __name__ == '__main__':
    unittest.main()
