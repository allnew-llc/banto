from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest

from banto.sync.x_oauth_pkce import (
    REQUIRED_SCOPES,
    XOAuthPKCEError,
    _pkce_challenge,
    _retire_previous_grant,
    _validate_exact_scopes,
    build_authorization_url,
)
from banto.sync.x_oauth_rotator import XOAuthRotatorError


def test_authorization_url_uses_exact_scopes_and_s256():
    challenge = _pkce_challenge("v" * 64)
    url = build_authorization_url(
        client_id="public-client-id",
        redirect_uri="http://127.0.0.1:8765/callback",
        state="random-state",
        code_challenge=challenge,
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "x.com"
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/callback"]
    assert query["scope"] == [" ".join(REQUIRED_SCOPES)]
    assert query["state"] == ["random-state"]
    assert query["code_challenge_method"] == ["S256"]
    assert "client-secret" not in url


def test_authorization_url_rejects_scope_expansion():
    with pytest.raises(XOAuthPKCEError, match="approved least-privilege"):
        build_authorization_url(
            client_id="client-id",
            redirect_uri="http://127.0.0.1:8765/callback",
            state="state",
            code_challenge="challenge",
            scopes=REQUIRED_SCOPES + ("dm.write",),
        )


def test_exact_scope_readback_rejects_missing_or_extra_scope():
    assert _validate_exact_scopes(" ".join(reversed(REQUIRED_SCOPES))) == REQUIRED_SCOPES
    with pytest.raises(XOAuthPKCEError, match="does not match"):
        _validate_exact_scopes("tweet.read tweet.write users.read offline.access")
    with pytest.raises(XOAuthPKCEError, match="does not match"):
        _validate_exact_scopes(" ".join(REQUIRED_SCOPES + ("dm.write",)))


@patch("banto.sync.x_oauth_pkce._access_token_is_inactive")
@patch("banto.sync.x_oauth_pkce._revoke_token")
def test_previous_grant_retires_refresh_and_active_access(revoke, inactive):
    inactive.side_effect = [False, True]

    result = _retire_previous_grant(
        old_access="old-access",
        old_refresh="old-refresh",
        client_id="client-id",
        client_secret="client-secret",
    )

    assert result == (True, True)
    assert revoke.call_args_list[0].args[0] == "old-refresh"
    assert revoke.call_args_list[1].args[0] == "old-access"


@patch("banto.sync.x_oauth_pkce._access_token_is_inactive")
@patch("banto.sync.x_oauth_pkce._revoke_token")
def test_previous_grant_accepts_already_invalid_refresh_then_retires_access(revoke, inactive):
    revoke.side_effect = [
        XOAuthRotatorError(
            "X OAuth request failed (HTTP 400, error=invalid_request, hint=token_rejected)."
        ),
        None,
    ]
    inactive.side_effect = [False, True]

    assert _retire_previous_grant(
        old_access="old-access",
        old_refresh="old-refresh",
        client_id="client-id",
        client_secret="client-secret",
    ) == (True, True)
