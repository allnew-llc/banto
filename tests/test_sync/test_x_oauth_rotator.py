# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
import io
import urllib.error
from unittest.mock import patch

import pytest

from banto.sync.x_oauth_rotator import (
    XOAuthRotatorError,
    _request_json,
    _revoke_token,
    rotate_x_oauth_user_token,
)


class FakeKeychain:
    def __init__(self):
        self.values = {
            "access-token": "old-access",
            "refresh-token": "old-refresh",
            "oauth-client-id": "client-id",
            "oauth-client-secret": "client-secret",
        }

    def get(self, name):
        return self.values.get(name)

    def store(self, name, value):
        self.values[name] = value
        return True


@patch("banto.sync.x_oauth_rotator.KeychainStore")
@patch("banto.sync.x_oauth_rotator._revoke_token")
@patch("banto.sync.x_oauth_rotator._verify_account")
@patch("banto.sync.x_oauth_rotator._request_json")
def test_closed_loop_rotation(request_json, verify_account, revoke_token, keychain_store):
    keychain_store.return_value = FakeKeychain()
    request_json.return_value = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 7200,
        "scope": "tweet.read tweet.write users.read media.write offline.access",
    }
    verify_account.return_value = ("allnew_llc", "123")

    result = rotate_x_oauth_user_token()

    assert result.username == "allnew_llc"
    assert result.user_id == "123"
    assert result.refresh_rotated is True
    assert result.previous_access_revoked is True
    assert result.previous_access_inactive is True
    assert result.persisted_readback_verified is True
    assert result.previous_access_fingerprint != result.current_access_fingerprint
    assert request_json.call_args.kwargs["form"]["client_id"] == "client-id"
    assert verify_account.call_count == 2
    revoke_token.assert_called_once_with("old-access", "client-id", "client-secret")


@patch("banto.sync.x_oauth_rotator._request_json")
def test_confidential_client_revocation_uses_basic_auth(request_json):
    _revoke_token("token-value", "client-id", "client-secret")

    call = request_json.call_args
    assert call.kwargs["headers"]["Authorization"].startswith("Basic ")
    assert call.kwargs["form"] == {"token": "token-value"}
    assert "client-secret" not in call.kwargs["form"]


@patch("banto.sync.x_oauth_rotator.KeychainStore")
@patch("banto.sync.x_oauth_rotator._request_json")
def test_rotation_rejects_scope_mismatch_before_keychain_write(request_json, keychain_store):
    keychain = FakeKeychain()
    keychain_store.return_value = keychain
    request_json.return_value = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 7200,
        "scope": "tweet.read tweet.write users.read offline.access dm.write",
    }

    with pytest.raises(XOAuthRotatorError, match="least-privilege"):
        rotate_x_oauth_user_token()

    assert keychain.values["access-token"] == "old-access"
    assert keychain.values["refresh-token"] == "old-refresh"


def test_provider_error_body_never_exposes_credential_sentinel(capsys):
    sentinel = "DO_NOT_EXPOSE_X_CREDENTIAL_SENTINEL"
    body = (
        '{"error":"invalid_request","error_description":'
        f'"refresh_token {sentinel} was rejected"}}'
    ).encode()
    error = urllib.error.HTTPError(
        "https://api.x.com/2/oauth2/token",
        400,
        "Bad Request",
        {},
        io.BytesIO(body),
    )

    with (
        patch("banto.sync.x_oauth_rotator.urllib.request.urlopen", side_effect=error),
        pytest.raises(XOAuthRotatorError) as captured,
    ):
        _request_json(
            "POST",
            "https://api.x.com/2/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            form={"grant_type": "refresh_token"},
        )

    output = capsys.readouterr()
    combined = str(captured.value) + repr(captured.value) + output.out + output.err
    assert sentinel not in combined
