import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
from PIL import Image

import pkce_core as oauth
import portrait_geometry as geo


class GeometryTests(unittest.TestCase):
    def test_canvas(self):
        self.assertEqual((geo.WIDTH, geo.HEIGHT), (600, 1024))

    def test_clockwise_corners(self):
        for panel, portrait in [((0, 0), (599, 0)), ((1023, 0), (599, 1023)),
                                 ((0, 599), (0, 0)), ((1023, 599), (0, 1023))]:
            with self.subTest(panel=panel):
                self.assertEqual(geo.panel_to_portrait(*panel), portrait)

    def test_counterclockwise_corners(self):
        for panel, portrait in [((0, 0), (0, 1023)), ((1023, 0), (0, 0)),
                                 ((0, 599), (599, 1023)), ((1023, 599), (599, 0))]:
            with self.subTest(panel=panel):
                self.assertEqual(geo.panel_to_portrait(*panel, 'ccw'), portrait)

    def test_inverse_sample_grid(self):
        for orientation in ('cw', 'ccw'):
            for x in range(0, 600, 13):
                for y in range(0, 1024, 17):
                    panel = geo.portrait_to_panel(x, y, orientation)
                    self.assertEqual(geo.panel_to_portrait(*panel, orientation), (x, y))

    def test_reject_out_of_bounds(self):
        for function, width, height in [(geo.portrait_to_panel, 600, 1024),
                                         (geo.panel_to_portrait, 1024, 600)]:
            for x, y in [(-1, 0), (0, -1), (width, 0), (0, height)]:
                with self.assertRaises(ValueError):
                    function(x, y)

    def test_image_rotation_matches_touch(self):
        source = Image.new('RGB', (600, 1024))
        points = [(0, 0), (599, 0), (0, 1023), (599, 1023), (126, 431)]
        for i, point in enumerate(points):
            source.putpixel(point, (40 * i, 100, 200))
        for orientation in ('cw', 'ccw'):
            panel = geo.image_to_panel(source, orientation)
            self.assertEqual(panel.size, (1024, 600))
            for point in points:
                self.assertEqual(panel.getpixel(geo.portrait_to_panel(*point, orientation)),
                                 source.getpixel(point))

    def test_reject_wrong_size_and_orientation(self):
        for function in (geo.panel_to_portrait, geo.portrait_to_panel):
            with self.assertRaises(ValueError):
                function(0, 0, 'unknown')
        with self.assertRaises(ValueError):
            geo.image_to_panel(Image.new('RGB', (1024, 600)))
        with self.assertRaises(ValueError):
            geo.image_to_panel(Image.new('RGB', (600, 1024)), 'unknown')


class PkceTests(unittest.TestCase):
    def test_rfc7636_public_example(self):
        # Published test vector, never a production verifier.
        self.assertEqual(oauth.challenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'),
                         'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')

    def test_verifier_validation(self):
        for verifier in ('short', 'x' * 129, ' ' * 64, 'あ' * 64):
            with self.assertRaises(ValueError):
                oauth.challenge(verifier)

    def test_request_is_pkce_read_only(self):
        auth = oauth.prepare('a' * 32)
        parsed = urlsplit(auth.url)
        params = parse_qs(parsed.query)
        self.assertEqual((parsed.scheme, parsed.netloc), ('https', 'accounts.spotify.com'))
        self.assertEqual(params['redirect_uri'], [oauth.REDIRECT_URI])
        self.assertEqual(params['response_type'], ['code'])
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertEqual(params['code_challenge'], [oauth.challenge(auth.verifier)])
        self.assertEqual(set(params['scope'][0].split()), set(oauth.SCOPES))
        self.assertNotIn('client_secret', params)
        self.assertNotIn('code_verifier', params)
        self.assertTrue(all('read' in scope for scope in oauth.SCOPES))

    def test_random_and_not_in_repr(self):
        first, second = oauth.prepare('a' * 32), oauth.prepare('a' * 32)
        self.assertNotEqual(first.verifier, second.verifier)
        self.assertNotEqual(first.state, second.state)
        self.assertGreaterEqual(len(first.verifier), 43)
        self.assertLessEqual(len(first.verifier), 128)
        self.assertGreaterEqual(len(first.state), 32)
        self.assertEqual(repr(first), 'Authorization()')

    def test_reject_wrong_client_id(self):
        for value in ('', 'not-an-id', 'a' * 31, 'g' * 32, 'a' * 64):
            with self.assertRaises(ValueError):
                oauth.prepare(value)

    def test_callback(self):
        target = '/callback?' + urlencode({'state': 'test-state', 'code': 'test-code'})
        self.assertEqual(oauth.callback_code(target, 'test-state'), 'test-code')

    def test_reject_wrong_state_and_targets(self):
        for target in ['/callback?state=wrong&code=test-code', '/callback?code=test-code',
                       'http://evil.example/callback?state=s&code=c',
                       '//evil.example/callback?state=s&code=c',
                       '/other?state=s&code=c', '/callback?state=s&code=c#extra',
                       '/callback?state=%E3%81%82&code=c']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                oauth.callback_code(target, 's')

    def test_duplicate_blank_denial_and_control_character(self):
        for query in ['state=s&state=s&code=c', 'state=s&code=c&code=d',
                      'state=s&code=', 'state=s&error=access_denied',
                      'state=s&code=c&error=access_denied', 'state=s&code=%0A',
                      'state=s&code=' + 'x' * 4097, 'state=s&code=c&' + 'x=y&' * 10]:
            with self.assertRaises(ValueError):
                oauth.callback_code('/callback?' + query, 's')

    def test_oversize_and_empty_expected_state(self):
        with self.assertRaises(ValueError):
            oauth.callback_code('/callback?' + 'x' * 17000, 's')
        with self.assertRaises(ValueError):
            oauth.callback_code('/callback?state=&code=c', '')


if __name__ == '__main__':
    unittest.main()
