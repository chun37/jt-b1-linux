"""OAuth PKCE primitives only. No listener, login, token exchange or persistence.

Soloist's device key is deliberately not used here. Read-only Web API scopes
supply catalog/library metadata; playback remains on Soloist's local API.
Never log the verifier, callback code, or future access/refresh tokens.
"""
import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

REDIRECT_URI = "http://127.0.0.1:8768/callback"
class AuthorizationDeclined(ValueError):
    """The user/issuer rejected this flow with a valid state."""


SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
    "user-follow-read",
)


def challenge(verifier):
    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise ValueError("invalid PKCE verifier")
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class Authorization:
    verifier: str = field(repr=False)
    state: str = field(repr=False)
    url: str = field(repr=False)


def prepare(client_id):
    if not re.fullmatch(r"[0-9a-fA-F]{32}", client_id):
        raise ValueError("expected a Spotify Web API Client ID, not a secret/key")
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    query = urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge(verifier),
        "show_dialog": "true",
    })
    return Authorization(verifier, state, "https://accounts.spotify.com/authorize?" + query)


def callback_code(request_target, expected_state):
    """Validate the relative HTTP request target of a future loopback handler.

    The future listener must separately enforce bind address and Host. This
    function must never be used as a request logger or a CLI accepting codes.
    """
    if len(request_target) > 16384:
        raise ValueError("callback too large")
    if not request_target.startswith("/callback?"):
        raise ValueError("unexpected callback target")
    parsed = urlsplit(request_target)
    if parsed.scheme or parsed.netloc or parsed.path != "/callback" or parsed.fragment:
        raise ValueError("unexpected callback target")
    params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
    if any(len(v) != 1 for v in params.values()):
        raise ValueError("duplicate callback field")
    state = params.get("state", [""])[0]
    if not expected_state or not hmac.compare_digest(state.encode(), expected_state.encode()):
        raise ValueError("callback state mismatch")
    if "error" in params:
        raise AuthorizationDeclined("Spotify authorization was declined or failed")
    code = params.get("code", [""])[0]
    if not code or len(code) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in code):
        raise ValueError("invalid authorization code")
    return code
