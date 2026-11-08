"""PC-only, short-lived Spotify PKCE helper. Credentials never enter stdout.

Run with a public client.json and a private state directory outside the project.
The server is loopback-only and exits after one valid callback or a deadline.
It does not touch JT-B1, Soloist, audio settings, or Spotify library contents.
"""
import argparse
import fcntl
import json
import os
import signal
import stat
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pkce_core import AuthorizationDeclined, REDIRECT_URI, SCOPES, callback_code, prepare

TOKEN_URL = "https://accounts.spotify.com/api/token"
MAX_JSON = 65536


class AuthFailure(Exception):
    """A sanitized error; never include remote bodies or credentials."""


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AuthFailure("state directory is not an owned, real directory")
    path.chmod(0o700)


def atomic_json(path, value, *, overwrite=True):
    if path.is_symlink():
        raise AuthFailure("refusing a symlink state file")
    fd, temporary = tempfile.mkstemp(prefix=".oauth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Atomic no-clobber publication, including concurrent file creation.
            os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise AuthFailure("token endpoint unexpectedly redirected")


def exchange_token(client_id, code, verifier):
    request = Request(TOKEN_URL, data=urlencode({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }).encode("ascii"), headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            if response.status != 200:
                raise AuthFailure("token endpoint returned an unexpected status")
            content = response.read(MAX_JSON + 1)
    except HTTPError as error:
        # Do not read, log or interpolate the response body/request data.
        status_code = error.code
        error.close()
        raise AuthFailure("token exchange HTTP status " + str(status_code)) from None
    except (URLError, TimeoutError, OSError):
        raise AuthFailure("token exchange network failure") from None
    if len(content) > MAX_JSON:
        raise AuthFailure("token response too large")
    try:
        return json.loads(content)
    except (ValueError, UnicodeError):
        raise AuthFailure("token response is not valid JSON") from None


def validated_token(payload, client_id):
    if not isinstance(payload, dict):
        raise AuthFailure("token response is not an object")
    for field in ("access_token", "refresh_token"):
        value = payload.get(field)
        if not isinstance(value, str) or not 1 <= len(value) <= 16384:
            raise AuthFailure("token response is missing credentials")
        if any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise AuthFailure("token response contains invalid credentials")
    token_type = payload.get("token_type", "")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise AuthFailure("unsupported token type")
    expires = payload.get("expires_in")
    if type(expires) is not int or not 0 < expires <= 604800:
        raise AuthFailure("invalid token lifetime")
    # RFC 6749 permits omission when the granted scope is unchanged.
    scope = payload.get("scope", " ".join(SCOPES))
    if not isinstance(scope, str) or set(SCOPES) != set(scope.split()):
        raise AuthFailure("granted permissions do not match the requested read-only set")
    received = int(time.time())
    return {"client_id": client_id, "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"], "token_type": "Bearer",
            "scope": scope, "received_at": received, "expires_at": received + expires}


class Flow:
    def __init__(self, client_id, directory, exchanger=exchange_token):
        self.client_id = client_id
        self.directory = directory
        self.authorization = prepare(client_id)
        self.exchanger = exchanger
        self.consumed = False
        self.done = False
        self.status = "waiting_for_user"

    def record(self, status):
        self.status = status
        atomic_json(self.directory / "result.json", {"status": status, "updated_at": int(time.time())})

    def complete(self, code):
        if self.consumed:
            raise AuthFailure("authorization callback was already consumed")
        # Never retry an authorization-code exchange automatically.
        self.consumed = True
        self.done = True
        target = self.directory / "web-api-token.json"
        if target.exists() or target.is_symlink():
            raise AuthFailure("refusing to overwrite existing credentials")
        token = validated_token(self.exchanger(self.client_id, code, self.authorization.verifier), self.client_id)
        atomic_json(target, token, overwrite=False)
        self.record("authorized")


class OAuthServer(HTTPServer):
    def __init__(self, flow, address=("127.0.0.1", 8768)):
        if address[0] != "127.0.0.1":
            raise AuthFailure("only IPv4 loopback binding is permitted")
        self.flow = flow
        super().__init__(address, Handler)
        self.expected_host = "127.0.0.1:" + str(self.server_port)
        self.timeout = 0.5

    def handle_error(self, request, client_address):
        # The default implementation can write request tracebacks to stderr.
        print("request failed (details suppressed)", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "JT-B1-PKCE"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        # In particular, never log the callback URL containing an OAuth code.
        pass

    def reply(self, status, content, location=None, content_type="text/html; charset=utf-8"):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if location is not None:
            self.send_header("Location", location)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionError, TimeoutError):
                pass

    def do_GET(self):
        if self.headers.get_all("Host", []) != [self.server.expected_host]:
            self.reply(403, "Invalid Host")
            return
        flow = self.server.flow
        if self.path == "/health":
            self.reply(200, json.dumps({"status": flow.status}), content_type="application/json")
            return
        if self.path == "/":
            self.reply(200, '<!doctype html><meta charset="utf-8"><title>JT-B1 GUI 認証</title>'
                       '<h1>検索とライブラリーの読み取り認証</h1>'
                       '<p>JT-B1で再生しているのと同じSpotifyアカウントで認証してください。</p>'
                       '<p>保存曲、アルバム、フォロー中のアーティストとプレイリストを読み取ります。'
                       'ライブラリー内容や音量は変更しません。</p>'
                       '<p>認証情報はこのPCの非公開ファイルへ保存します。チャットへの貼り付けは不要です。</p>'
                       '<p><a href="/authorize">Spotifyで認証する</a></p>')
            return
        if self.path == "/authorize":
            if flow.consumed:
                self.reply(409, "This authorization flow has ended.")
            else:
                self.reply(302, "", location=flow.authorization.url)
            return
        if not self.path.startswith("/callback?"):
            self.reply(404, "Not found")
            return
        if flow.consumed:
            self.reply(409, "This authorization flow has ended.")
            return
        try:
            code = callback_code(self.path, flow.authorization.state)
        except AuthorizationDeclined:
            flow.consumed = flow.done = True
            flow.record("denied")
            self.reply(400, '<meta charset="utf-8"><p>認証が拒否されました。許可の変更は行っていません。</p>')
            return
        except ValueError:
            self.reply(400, "Invalid callback; credentials were not exchanged.")
            return
        try:
            flow.complete(code)
        except Exception:
            # Even unexpected exception messages may contain credentials.
            flow.record("exchange_failed")
            self.reply(502, '<meta charset="utf-8"><p>認証を完了できませんでした。codeやtokenは貼らず、認証失敗と伝えてください。</p>')
            return
        self.reply(200, '<!doctype html><meta charset="utf-8"><title>認証完了</title>'
                   '<h1>認証できました</h1><p>認証情報をPCの非公開ディレクトリーへ保存しました。この画面を閉じてください。</p>'
                   '<p>code、tokenやClient Secretを送る必要はありません。</p>')

    def do_POST(self):
        self.reply(405, "GET only")

    do_HEAD = do_POST
    do_OPTIONS = do_POST


def _deadline_expired(signum, frame):
    raise TimeoutError("authorization helper deadline reached")


def run(client_file, state_dir, lifetime):
    private_directory(state_dir)
    lock_fd = os.open(state_dir / "oauth.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    previous_alarm_handler = None
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (state_dir / "web-api-token.json").exists() or (state_dir / "web-api-token.json").is_symlink():
            raise AuthFailure("credentials already exist; refusing to overwrite")
        config = json.loads(client_file.read_text())
        if config.get("redirect_uri") != REDIRECT_URI:
            raise AuthFailure("registered redirect URI must match the helper")
        flow = Flow(config["client_id"], state_dir)
        server = OAuthServer(flow)
        with server:
            atomic_json(state_dir / "run.json", {"pid": os.getpid(), "started_at": int(time.time()),
                                                "expires_at": int(time.time()) + lifetime,
                                                "client_id": flow.client_id})
            flow.record("waiting_for_user")
            print("ready: http://127.0.0.1:8768/ (valid for " + str(lifetime) + " seconds)", flush=True)
            deadline = time.monotonic() + lifetime
            if lifetime > 0:
                previous_alarm_handler = signal.signal(signal.SIGALRM, _deadline_expired)
                signal.setitimer(signal.ITIMER_REAL, lifetime)
            try:
                while not flow.done and time.monotonic() < deadline:
                    server.handle_request()
            except TimeoutError:
                pass
            finally:
                if previous_alarm_handler is not None:
                    signal.setitimer(signal.ITIMER_REAL, 0)
            if time.monotonic() >= deadline or not flow.done:
                flow.record("expired")
            print("status: " + flow.status, flush=True)
            return 0 if flow.status == "authorized" else 1
    finally:
        if previous_alarm_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_alarm_handler)
        os.close(lock_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--lifetime", type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.lifetime <= 1800:
        parser.error("lifetime must be 1..1800 seconds")
    os.umask(0o077)
    try:
        return run(args.client_file, args.state_dir, args.lifetime)
    except Exception:
        print("helper could not start (port/state/configuration conflict; details suppressed)", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
