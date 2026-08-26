# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Closed-loop X OAuth 2.0 user-token rotation without secret disclosure."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..keychain import KeychainStore

X_API_BASE = "https://api.x.com"
DEFAULT_SERVICE_PREFIX = "allnew-x"
DEFAULT_ACCOUNT = "allnew_llc"
REQUIRED_X_OAUTH_SCOPES = (
    "tweet.read",
    "tweet.write",
    "users.read",
    "media.write",
    "offline.access",
)


class XOAuthRotatorError(RuntimeError):
    """Raised when X OAuth rotation cannot complete safely."""


@dataclass(frozen=True)
class XOAuthRotationResult:
    username: str
    user_id: str
    expires_in: int | None
    scope: str
    previous_access_fingerprint: str
    current_access_fingerprint: str
    refresh_rotated: bool
    previous_access_revoked: bool
    previous_access_inactive: bool
    persisted_readback_verified: bool


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _oauth_error_code(body: bytes) -> tuple[str, str]:
    try:
        payload = json.loads(body.decode("utf-8"))
        value = payload.get("error", "unknown")
        description = str(payload.get("error_description") or "").lower()
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        value = "unknown"
        description = ""
    value = str(value)
    code = value if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) else "unknown"
    known_parameter = next(
        (
            name
            for name in ("refresh_token", "grant_type", "client_id", "token")
            if name in description
        ),
        "",
    )
    if known_parameter:
        hint = f"{known_parameter}_rejected"
    elif "refresh" in description and any(word in description for word in ("invalid", "expired", "revoked")):
        hint = "refresh_credential_unusable"
    elif "client" in description and any(word in description for word in ("invalid", "missing", "required")):
        hint = "client_authentication_rejected"
    elif "grant" in description and "invalid" in description:
        hint = "grant_rejected"
    elif "missing" in description or "required" in description:
        hint = "required_parameter_rejected"
    else:
        hint = "unspecified"
    return code, hint


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    form: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict:
    body = urllib.parse.urlencode(form).encode("utf-8") if form is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_code, error_hint = _oauth_error_code(exc.read())
        raise XOAuthRotatorError(
            f"X OAuth request failed (HTTP {exc.code}, error={error_code}, hint={error_hint})."
        ) from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise XOAuthRotatorError(
            f"X OAuth request failed ({type(exc).__name__})."
        ) from None
    if not isinstance(payload, dict):
        raise XOAuthRotatorError("X OAuth response was not a JSON object.")
    return payload


def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _validate_exact_scopes(value: object) -> str:
    if not isinstance(value, str):
        raise XOAuthRotatorError("X OAuth response omitted the approved scope set.")
    scopes = tuple(part for part in value.split() if part)
    if (
        len(scopes) != len(REQUIRED_X_OAUTH_SCOPES)
        or set(scopes) != set(REQUIRED_X_OAUTH_SCOPES)
    ):
        raise XOAuthRotatorError(
            "X OAuth scope read-back does not match the approved least-privilege set."
        )
    return " ".join(REQUIRED_X_OAUTH_SCOPES)


def _verify_account(access_token: str, expected_username: str) -> tuple[str, str]:
    payload = _request_json(
        "GET",
        f"{X_API_BASE}/2/users/me?user.fields=username",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "banto-sync"},
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise XOAuthRotatorError("X account read-back did not contain account data.")
    username = str(data.get("username") or "")
    user_id = str(data.get("id") or "")
    if username.lower() != expected_username.lower() or not user_id:
        raise XOAuthRotatorError("X authenticated-account read-back mismatch.")
    return username, user_id


def _revoke_token(token: str, client_id: str, client_secret: str) -> None:
    _request_json(
        "POST",
        f"{X_API_BASE}/2/oauth2/revoke",
        headers={
            "Authorization": _basic_auth(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "banto-sync",
        },
        form={"token": token},
    )


def _access_token_is_inactive(access_token: str, *, timeout: int = 30) -> bool:
    request = urllib.request.Request(
        f"{X_API_BASE}/2/users/me",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "banto-sync"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return False
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code == 401
    except (urllib.error.URLError, TimeoutError) as exc:
        raise XOAuthRotatorError(
            f"Could not verify previous X token inactivity ({type(exc).__name__})."
        ) from None


def rotate_x_oauth_user_token(
    *,
    service_prefix: str = DEFAULT_SERVICE_PREFIX,
    account: str = DEFAULT_ACCOUNT,
    expected_username: str = DEFAULT_ACCOUNT,
    revoke_previous_access: bool = True,
) -> XOAuthRotationResult:
    """Refresh, verify, persist, revoke the prior token, and verify again."""
    keychain = KeychainStore(service_prefix=service_prefix, account=account)
    names = ("access-token", "refresh-token", "oauth-client-id", "oauth-client-secret")
    secrets = {name: keychain.get(name) for name in names}
    missing = [name for name, value in secrets.items() if not value]
    if missing:
        raise XOAuthRotatorError(
            "Required X OAuth Keychain items are missing: " + ", ".join(missing)
        )

    old_access = str(secrets["access-token"])
    old_refresh = str(secrets["refresh-token"])
    client_id = str(secrets["oauth-client-id"])
    client_secret = str(secrets["oauth-client-secret"])
    refresh_form = {
        "grant_type": "refresh_token",
        "refresh_token": old_refresh,
        "client_id": client_id,
    }
    try:
        payload = _request_json(
            "POST",
            f"{X_API_BASE}/2/oauth2/token",
            headers={
                "Authorization": _basic_auth(client_id, client_secret),
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "banto-sync",
            },
            form=refresh_form,
        )
    except XOAuthRotatorError as exc:
        if "HTTP 400, error=invalid_request" not in str(exc):
            raise
        payload = _request_json(
            "POST",
            f"{X_API_BASE}/2/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "banto-sync",
            },
            form=refresh_form,
        )
    new_access = str(payload.get("access_token") or "")
    new_refresh = str(payload.get("refresh_token") or "")
    if not new_access or not new_refresh:
        raise XOAuthRotatorError("X refresh response omitted the access/refresh token pair.")
    if new_access == old_access:
        raise XOAuthRotatorError("X returned the previous access token; rotation stopped.")

    approved_scope = _validate_exact_scopes(payload.get("scope"))

    username, user_id = _verify_account(new_access, expected_username)

    # Save the new refresh token first. If the second write fails, the refresh
    # credential needed for a safe retry is still recoverable from Keychain.
    if not keychain.store("refresh-token", new_refresh):
        raise XOAuthRotatorError("Failed to persist the refreshed X refresh token.")
    if keychain.get("refresh-token") != new_refresh:
        raise XOAuthRotatorError("X refresh-token Keychain read-back mismatch.")
    if not keychain.store("access-token", new_access):
        raise XOAuthRotatorError(
            "Refreshed X refresh token persisted, but access-token persistence failed."
        )
    if keychain.get("access-token") != new_access:
        raise XOAuthRotatorError("X access-token Keychain read-back mismatch.")

    revoked = False
    previous_inactive = False
    if revoke_previous_access:
        try:
            _revoke_token(old_access, client_id, client_secret)
            revoked = True
            previous_inactive = True
        except XOAuthRotatorError as exc:
            if "HTTP 400, error=invalid_request, hint=token_rejected" not in str(exc):
                raise
            previous_inactive = _access_token_is_inactive(old_access)
            if not previous_inactive:
                raise XOAuthRotatorError(
                    "X rejected revocation and the previous access token remains active."
                ) from None

    persisted_access = keychain.get("access-token")
    if persisted_access != new_access:
        raise XOAuthRotatorError("Persisted X access token changed before final read-back.")
    username, user_id = _verify_account(persisted_access, expected_username)

    expires_raw = payload.get("expires_in")
    expires_in = expires_raw if isinstance(expires_raw, int) else None
    return XOAuthRotationResult(
        username=username,
        user_id=user_id,
        expires_in=expires_in,
        scope=approved_scope,
        previous_access_fingerprint=_fingerprint(old_access),
        current_access_fingerprint=_fingerprint(new_access),
        refresh_rotated=new_refresh != old_refresh,
        previous_access_revoked=revoked,
        previous_access_inactive=previous_inactive,
        persisted_readback_verified=True,
    )
