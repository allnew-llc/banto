# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Least-privilege X OAuth 2.0 Authorization Code + PKCE enrollment."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

from ..keychain import KeychainStore
from .x_oauth_rotator import (
    DEFAULT_ACCOUNT,
    DEFAULT_SERVICE_PREFIX,
    REQUIRED_X_OAUTH_SCOPES,
    X_API_BASE,
    XOAuthRotatorError,
    _access_token_is_inactive,
    _basic_auth,
    _request_json,
    _revoke_token,
    _verify_account,
)

X_AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
REQUIRED_SCOPES = REQUIRED_X_OAUTH_SCOPES


class XOAuthPKCEError(RuntimeError):
    """Raised when least-privilege X OAuth enrollment cannot complete safely."""


@dataclass(frozen=True)
class XOAuthPKCEResult:
    username: str
    user_id: str
    expires_in: int | None
    scopes: tuple[str, ...]
    previous_grant_retired: bool
    previous_access_inactive: bool
    persisted_readback_verified: bool


@dataclass
class _CallbackResult:
    event: threading.Event
    code: str = ""
    error: str = ""


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: tuple[str, ...] = REQUIRED_SCOPES,
) -> str:
    """Build an X authorization URL with a fixed, least-privilege scope set."""
    if tuple(scopes) != REQUIRED_SCOPES:
        raise XOAuthPKCEError("X OAuth scopes must match the approved least-privilege set.")
    parsed = urllib.parse.urlsplit(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/callback":
        raise XOAuthPKCEError("X OAuth redirect URI must use the approved loopback callback.")
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{X_AUTHORIZE_URL}?{query}"


def _scope_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(part for part in value.split() if part)


def _validate_exact_scopes(value: object) -> tuple[str, ...]:
    scopes = _scope_tuple(value)
    if len(scopes) != len(REQUIRED_SCOPES) or set(scopes) != set(REQUIRED_SCOPES):
        raise XOAuthPKCEError("X returned a scope set that does not match the approved set.")
    return tuple(scope for scope in REQUIRED_SCOPES if scope in scopes)


def _make_callback_handler(expected_state: str, result: _CallbackResult):
    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            returned_state = (query.get("state") or [""])[0]
            code = (query.get("code") or [""])[0]
            oauth_error = (query.get("error") or [""])[0]
            if not secrets.compare_digest(returned_state, expected_state):
                result.error = "state_mismatch"
                status = 400
                heading = "認可を完了できませんでした"
                message = "状態確認に失敗しました。Codexに戻ってください。"
            elif oauth_error:
                result.error = "authorization_denied"
                status = 400
                heading = "認可は完了していません"
                message = "許可されませんでした。Codexに戻ってください。"
            elif not code:
                result.error = "authorization_code_missing"
                status = 400
                heading = "認可を完了できませんでした"
                message = "認可コードを受信できませんでした。Codexに戻ってください。"
            else:
                result.code = code
                status = 200
                heading = "認可を受け取りました"
                message = "このタブを閉じてCodexに戻ってください。"
            body = (
                "<!doctype html><html lang='ja'><meta charset='utf-8'>"
                f"<title>{heading}</title><body><h1>{heading}</h1><p>{message}</p></body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            result.event.set()

    return CallbackHandler


def _retire_previous_grant(
    *,
    old_access: str,
    old_refresh: str,
    client_id: str,
    client_secret: str,
) -> tuple[bool, bool]:
    """Revoke both prior credentials and prove the old access token is inactive."""
    try:
        _revoke_token(old_refresh, client_id, client_secret)
    except XOAuthRotatorError as exc:
        if "HTTP 400, error=invalid_request, hint=token_rejected" not in str(exc):
            raise XOAuthPKCEError("Could not retire the previous X refresh credential.") from exc

    inactive = _access_token_is_inactive(old_access)
    if not inactive:
        try:
            _revoke_token(old_access, client_id, client_secret)
        except XOAuthRotatorError as exc:
            if "HTTP 400, error=invalid_request, hint=token_rejected" not in str(exc):
                raise XOAuthPKCEError("Could not retire the previous X access credential.") from exc
        inactive = _access_token_is_inactive(old_access)
    if not inactive:
        raise XOAuthPKCEError("The previous X access credential remains active.")
    return True, True


def authorize_x_oauth_user_token(
    *,
    service_prefix: str = DEFAULT_SERVICE_PREFIX,
    account: str = DEFAULT_ACCOUNT,
    expected_username: str = DEFAULT_ACCOUNT,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    timeout_seconds: int = 300,
    open_browser: bool = True,
) -> XOAuthPKCEResult:
    """Retire the broad grant, obtain exact scopes, verify, and persist the pair."""
    keychain = KeychainStore(service_prefix=service_prefix, account=account)
    names = ("access-token", "refresh-token", "oauth-client-id", "oauth-client-secret")
    values = {name: keychain.get(name) for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise XOAuthPKCEError("Required X OAuth Keychain items are missing: " + ", ".join(missing))

    old_access = str(values["access-token"])
    old_refresh = str(values["refresh-token"])
    client_id = str(values["oauth-client-id"])
    client_secret = str(values["oauth-client-secret"])

    parsed = urllib.parse.urlsplit(redirect_uri)
    if parsed.port is None:
        raise XOAuthPKCEError("The X OAuth loopback callback must include a port.")

    callback_result = _CallbackResult(event=threading.Event())
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _pkce_challenge(verifier)
    authorization_url = build_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=challenge,
    )
    server = HTTPServer(
        ("127.0.0.1", parsed.port),
        _make_callback_handler(state, callback_result),
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_started = False

    try:
        retired, previous_inactive = _retire_previous_grant(
            old_access=old_access,
            old_refresh=old_refresh,
            client_id=client_id,
            client_secret=client_secret,
        )
        server_thread.start()
        server_started = True
        if open_browser and not webbrowser.open(authorization_url):
            raise XOAuthPKCEError("Could not open the X authorization page.")
        if not callback_result.event.wait(timeout_seconds):
            raise XOAuthPKCEError("Timed out waiting for X authorization.")
    finally:
        if server_started:
            server.shutdown()
        server.server_close()
        if server_started and server_thread.is_alive():
            server_thread.join(timeout=2)

    if callback_result.error:
        raise XOAuthPKCEError(f"X authorization callback failed ({callback_result.error}).")
    if not callback_result.code:
        raise XOAuthPKCEError("X authorization callback omitted the authorization code.")

    try:
        payload = _request_json(
            "POST",
            f"{X_API_BASE}/2/oauth2/token",
            headers={
                "Authorization": _basic_auth(client_id, client_secret),
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "banto-sync",
            },
            form={
                "grant_type": "authorization_code",
                "code": callback_result.code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
    except XOAuthRotatorError as exc:
        raise XOAuthPKCEError("X authorization-code exchange failed.") from exc

    new_access = str(payload.get("access_token") or "")
    new_refresh = str(payload.get("refresh_token") or "")
    if not new_access or not new_refresh:
        raise XOAuthPKCEError("X authorization response omitted the access/refresh token pair.")
    scopes = _validate_exact_scopes(payload.get("scope"))
    username, user_id = _verify_account(new_access, expected_username)

    if not keychain.store("refresh-token", new_refresh):
        raise XOAuthPKCEError("Failed to persist the new X refresh credential.")
    if keychain.get("refresh-token") != new_refresh:
        raise XOAuthPKCEError("X refresh credential Keychain read-back mismatch.")
    if not keychain.store("access-token", new_access):
        raise XOAuthPKCEError("Refresh credential persisted, but access persistence failed.")
    if keychain.get("access-token") != new_access:
        raise XOAuthPKCEError("X access credential Keychain read-back mismatch.")

    persisted_access = keychain.get("access-token")
    if persisted_access != new_access:
        raise XOAuthPKCEError("Persisted X access credential changed before final read-back.")
    username, user_id = _verify_account(persisted_access, expected_username)
    expires_raw = payload.get("expires_in")
    return XOAuthPKCEResult(
        username=username,
        user_id=user_id,
        expires_in=expires_raw if isinstance(expires_raw, int) else None,
        scopes=scopes,
        previous_grant_retired=retired,
        previous_access_inactive=previous_inactive,
        persisted_readback_verified=True,
    )
