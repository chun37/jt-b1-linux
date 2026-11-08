"""Only synthetic tokens; no Spotify calls and no real credential reads."""
import contextlib
import http.client
import io
import json
import os
import stat
import socket
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit

import auth_helper as helper
from pkce_core import SCOPES

CLIENT_ID = 'a' * 32


def payload():
    return {'access_token': 'SYNTHETIC-ACCESS-TOKEN', 'refresh_token': 'SYNTHETIC-REFRESH-TOKEN',
            'token_type': 'Bearer', 'expires_in': 3600, 'scope': ' '.join(SCOPES)}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'private'
        helper.private_directory(self.directory)

    def test_directory_and_atomic_file_permissions(self):
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        path = self.directory / 'example.json'
        helper.atomic_json(path, {'example': 'synthetic'})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text()), {'example': 'synthetic'})
        self.assertEqual(list(self.directory.glob('.oauth-*')), [])

    def test_reject_directory_symlink_without_changing_target(self):
        target = Path(self.temp.name) / 'target'
        target.mkdir(mode=0o755)
        link = Path(self.temp.name) / 'link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(helper.AuthFailure):
            helper.private_directory(link)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_reject_file_symlink(self):
        target = self.directory / 'target'
        target.write_text('unchanged')
        link = self.directory / 'link'
        link.symlink_to(target)
        with self.assertRaises(helper.AuthFailure):
            helper.atomic_json(link, {'test': True})
        self.assertEqual(target.read_text(), 'unchanged')

    def test_failed_atomic_write_removes_temporary(self):
        target = self.directory / 'target.json'
        with patch.object(helper.os, 'replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                helper.atomic_json(target, payload())
        self.assertFalse(target.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_atomic_no_clobber(self):
        target = self.directory / 'target.json'
        helper.atomic_json(target, {'original': True}, overwrite=False)
        with self.assertRaises(FileExistsError):
            helper.atomic_json(target, payload(), overwrite=False)
        self.assertEqual(json.loads(target.read_text()), {'original': True})
        self.assertEqual(list(self.directory.glob('.oauth-*')), [])

    def test_concurrent_credential_creation_during_exchange_is_preserved(self):
        target = self.directory / 'web-api-token.json'
        def exchange(*args):
            target.write_text('concurrent-private-content')
            return payload()
        flow = helper.Flow(CLIENT_ID, self.directory, exchanger=exchange)
        with self.assertRaises(FileExistsError):
            flow.complete('synthetic-code')
        self.assertEqual(target.read_text(), 'concurrent-private-content')
        self.assertEqual(list(self.directory.glob('.oauth-*')), [])

    def test_complete_persists_without_printing_credentials(self):
        exchange = Mock(return_value=payload())
        flow = helper.Flow(CLIENT_ID, self.directory, exchanger=exchange)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            flow.complete('synthetic-code')
        self.assertEqual(output.getvalue(), '')
        token = json.loads((self.directory / 'web-api-token.json').read_text())
        self.assertEqual(token['client_id'], CLIENT_ID)
        self.assertGreater(token['expires_at'], token['received_at'])
        self.assertEqual(json.loads((self.directory / 'result.json').read_text())['status'], 'authorized')
        self.assertNotIn('SYNTHETIC', (self.directory / 'result.json').read_text())
        self.assertEqual(exchange.call_count, 1)
        with self.assertRaises(helper.AuthFailure):
            flow.complete('another-code')
        self.assertEqual(exchange.call_count, 1)

    def test_no_overwrite_of_existing_credentials(self):
        target = self.directory / 'web-api-token.json'
        target.write_text('existing-private-content')
        exchange = Mock(return_value=payload())
        flow = helper.Flow(CLIENT_ID, self.directory, exchanger=exchange)
        with self.assertRaises(helper.AuthFailure):
            flow.complete('synthetic-code')
        self.assertEqual(target.read_text(), 'existing-private-content')
        exchange.assert_not_called()

    def test_single_instance_lock(self):
        descriptor = os.open(self.directory / 'oauth.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            helper.fcntl.flock(descriptor, helper.fcntl.LOCK_EX | helper.fcntl.LOCK_NB)
            with patch.object(helper, 'OAuthServer') as server:
                with self.assertRaises(BlockingIOError):
                    helper.run(self.directory / 'nonexistent.json', self.directory, 1)
                server.assert_not_called()
        finally:
            os.close(descriptor)

    def test_expiry_records_safe_result_and_closes_listener(self):
        config = self.directory / 'client.json'
        config.write_text(json.dumps({'client_id': CLIENT_ID, 'redirect_uri': helper.REDIRECT_URI}))
        real_server = helper.OAuthServer
        with patch.object(helper, 'OAuthServer', side_effect=lambda flow: real_server(flow, ('127.0.0.1', 0))):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(helper.run(config, self.directory, 0), 1)
        self.assertEqual(json.loads((self.directory / 'result.json').read_text())['status'], 'expired')
        self.assertFalse((self.directory / 'web-api-token.json').exists())

    def test_absolute_deadline_interrupts_a_slow_incomplete_request(self):
        config = self.directory / 'client.json'
        config.write_text(json.dumps({'client_id': CLIENT_ID, 'redirect_uri': helper.REDIRECT_URI}))
        real_server = helper.OAuthServer
        threads = []
        stopping = threading.Event()
        def factory(flow):
            server = real_server(flow, ('127.0.0.1', 0))
            def slow_client():
                try:
                    with socket.create_connection(server.server_address, timeout=2) as connection:
                        connection.sendall(b'GET / HTTP/1.1\r\nHost: ')
                        for _ in range(50):
                            if stopping.wait(0.05):
                                break
                            connection.sendall(b'x')
                except OSError:
                    pass
            thread = threading.Thread(target=slow_client, daemon=True)
            thread.start()
            threads.append(thread)
            return server
        started = time.monotonic()
        try:
            with patch.object(helper, 'OAuthServer', side_effect=factory):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(helper.run(config, self.directory, 1), 1)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(json.loads((self.directory / 'result.json').read_text())['status'], 'expired')
        finally:
            stopping.set()
            for thread in threads:
                thread.join(timeout=2)

    def test_run_refuses_existing_credentials_before_listening(self):
        (self.directory / 'web-api-token.json').write_text('synthetic')
        with patch.object(helper, 'OAuthServer') as server:
            with self.assertRaises(helper.AuthFailure):
                helper.run(self.directory / 'nonexistent.json', self.directory, 1)
            server.assert_not_called()


class TokenTests(unittest.TestCase):
    def test_valid_token_and_default_scope(self):
        for include_scope in (True, False):
            value = payload()
            if not include_scope:
                del value['scope']
            result = helper.validated_token(value, CLIENT_ID)
            self.assertEqual(set(result['scope'].split()), set(SCOPES))

    def test_missing_malformed_and_excessive_permissions(self):
        for field, value in [('access_token', ''), ('access_token', 'line\nbreak'),
                             ('refresh_token', None), ('token_type', 'Other'),
                             ('token_type', 123), ('expires_in', True), ('expires_in', -1),
                             ('expires_in', 999999999), ('scope', 'user-library-read'),
                             ('scope', ' '.join(SCOPES) + ' playlist-modify-public')]:
            test = payload(); test[field] = value
            with self.subTest(field=field), self.assertRaises(helper.AuthFailure):
                helper.validated_token(test, CLIENT_ID)
        with self.assertRaises(helper.AuthFailure):
            helper.validated_token([], CLIENT_ID)

    def test_exchange_uses_post_pkce_without_secret(self):
        response = Mock(status=200)
        response.read.return_value = json.dumps(payload()).encode()
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = context
        with patch.object(helper, 'build_opener', return_value=opener):
            self.assertEqual(helper.exchange_token(CLIENT_ID, 'synthetic-code', 'v' * 64), payload())
        request = opener.open.call_args.args[0]
        form = parse_qs(request.data.decode())
        self.assertEqual(request.full_url, helper.TOKEN_URL)
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(form['code_verifier'], ['v' * 64])
        self.assertEqual(form['redirect_uri'], [helper.REDIRECT_URI])
        self.assertNotIn('client_secret', form)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 15)

    def test_bad_or_oversize_token_json_is_rejected(self):
        for body in (b'not json', b'\xff', b'x' * (helper.MAX_JSON + 1)):
            response = Mock(status=200)
            response.read.return_value = body
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            opener = Mock()
            opener.open.return_value = context
            with patch.object(helper, 'build_opener', return_value=opener):
                with self.assertRaises(helper.AuthFailure):
                    helper.exchange_token(CLIENT_ID, 'synthetic-code', 'v' * 64)

    def test_redirect_blocked(self):
        with self.assertRaises(helper.AuthFailure):
            helper.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://evil.example/')

    def test_exchange_errors_are_sanitized(self):
        for error in [HTTPError(helper.TOKEN_URL, 401, 'SYNTHETIC-SECRET', {}, io.BytesIO(b'SYNTHETIC-BODY')),
                      URLError('SYNTHETIC-SECRET')]:
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(helper, 'build_opener', return_value=opener):
                with self.assertRaises(helper.AuthFailure) as result:
                    helper.exchange_token(CLIENT_ID, 'synthetic-code', 'v' * 64)
            self.assertNotIn('SYNTHETIC', str(result.exception))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        helper.private_directory(self.directory)
        self.exchange = Mock(return_value=payload())
        self.flow = helper.Flow(CLIENT_ID, self.directory, exchanger=self.exchange)
        self.flow.record('waiting_for_user')
        self.server = helper.OAuthServer(self.flow, ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, hosts=None, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        connection.putrequest(method, path, skip_host=True)
        for host in hosts if hosts is not None else [self.server.expected_host]:
            connection.putheader('Host', host)
        connection.endheaders()
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read().decode()
        connection.close()
        return result

    def callback(self, **fields):
        return '/callback?' + urlencode({'state': self.flow.authorization.state, 'code': 'synthetic-code', **fields})

    def test_loopback_only(self):
        with self.assertRaises(helper.AuthFailure):
            helper.OAuthServer(self.flow, ('0.0.0.0', 0))
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_host_rebinding_missing_duplicate_and_wrong_port(self):
        for hosts in [[], ['evil.example'], ['localhost:' + str(self.server.server_port)],
                      ['127.0.0.1:1'], [self.server.expected_host, self.server.expected_host]]:
            self.assertEqual(self.request(self.callback(), hosts)[0], 403)
        self.exchange.assert_not_called()

    def test_portal_and_health_do_not_leak_or_start_exchange(self):
        for path in ('/', '/health'):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            for private in (self.flow.authorization.state, self.flow.authorization.verifier, 'SYNTHETIC'):
                self.assertNotIn(private, body)
        self.exchange.assert_not_called()

    def test_authorize_redirect_is_fixed_spotify_endpoint(self):
        status, headers, body = self.request('/authorize')
        self.assertEqual(status, 302)
        url = urlsplit(headers['Location'])
        self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'accounts.spotify.com', '/authorize'))
        self.assertNotIn(self.flow.authorization.verifier, headers['Location'])
        self.exchange.assert_not_called()

    def test_wrong_state_does_not_consume_flow(self):
        self.assertEqual(self.request(self.callback(state='wrong'))[0], 400)
        self.assertFalse(self.flow.consumed)
        self.assertEqual(self.request(self.callback())[0], 200)
        self.assertEqual(self.exchange.call_count, 1)

    def test_success_and_replay_no_logging_or_secret_in_html(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status, headers, body = self.request(self.callback())
            second = self.request(self.callback())[0]
        self.assertEqual((status, second), (200, 409))
        self.assertEqual(output.getvalue(), '')
        self.assertTrue(self.flow.done)
        self.assertEqual(self.exchange.call_count, 1)
        for private in ('synthetic-code', 'SYNTHETIC-ACCESS-TOKEN', 'SYNTHETIC-REFRESH-TOKEN', self.flow.authorization.verifier):
            self.assertNotIn(private, body)
        self.assertEqual(stat.S_IMODE((self.directory / 'web-api-token.json').stat().st_mode), 0o600)

    def test_valid_denial_consumes_without_exchange(self):
        path = '/callback?' + urlencode({'state': self.flow.authorization.state, 'error': 'access_denied'})
        self.assertEqual(self.request(path)[0], 400)
        self.assertTrue(self.flow.done)
        self.assertEqual(self.flow.status, 'denied')
        self.exchange.assert_not_called()

    def test_exchange_failure_is_terminal_and_does_not_log_exception(self):
        self.exchange.side_effect = ValueError('SYNTHETIC-SECRET')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status, headers, body = self.request(self.callback())
        self.assertEqual(status, 502)
        self.assertTrue(self.flow.done)
        self.assertEqual(self.flow.status, 'exchange_failed')
        self.assertNotIn('SYNTHETIC', body + output.getvalue())
        self.assertFalse((self.directory / 'web-api-token.json').exists())

    def test_bad_path_and_method(self):
        self.assertEqual(self.request('/unknown')[0], 404)
        self.assertEqual(self.request('/authorize', method='POST')[0], 405)
        self.exchange.assert_not_called()


if __name__ == '__main__':
    unittest.main()
