"""Token caching, refresh-only startup, and credential fallback tests."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.whisker_ting.api import (
    WhiskerApiClient,
    WhiskerAuthError,
    WhiskerConnectionError,
)
from custom_components.whisker_ting.auth import AuthenticationError, CognitoServiceError
import homeassistant.util.dt as dt_util


@pytest.fixture
def client() -> WhiskerApiClient:
    return WhiskerApiClient(MagicMock(), "u", "p")


async def test_valid_token_is_reused(client):
    client._access_token = "tok"
    client._token_expiry = dt_util.utcnow() + timedelta(hours=1)
    client._user_id = 123
    client._api_key = "api-key"
    client._authenticate = AsyncMock()
    assert await client._ensure_token() == "tok"
    client._authenticate.assert_not_awaited()


async def test_expiring_token_triggers_refresh(client):
    client._access_token = "old"
    client._token_expiry = dt_util.utcnow() + timedelta(minutes=1)  # < 5 min buffer
    client._refresh_token = None

    async def _auth():
        client._access_token = "new"
        client._token_expiry = dt_util.utcnow() + timedelta(hours=1)

    client._authenticate = AsyncMock(side_effect=_auth)
    assert await client._ensure_token() == "new"
    client._authenticate.assert_awaited_once()


async def test_refresh_only_startup_hydrates_account_attributes():
    client = WhiskerApiClient(MagicMock(), "u", refresh_token="stored-refresh-token")
    client._auth.refresh_tokens = AsyncMock(
        return_value={
            "AccessToken": "new-access-token",
            "IdToken": "new-id-token",
            "ExpiresIn": 3600,
        }
    )
    client._auth.get_user = AsyncMock(
        return_value={
            "UserAttributes": [
                {"Name": "custom:user_id", "Value": "123"},
                {"Name": "custom:api_key", "Value": "api-key"},
            ]
        }
    )

    assert await client._ensure_token() == "new-access-token"
    assert client.user_id == 123
    assert client.api_key == "api-key"
    assert client.refresh_token == "stored-refresh-token"
    client._auth.refresh_tokens.assert_awaited_once_with("stored-refresh-token")
    client._auth.get_user.assert_awaited_once_with("new-access-token")


async def test_rotated_refresh_token_is_published_for_persistence():
    """A replacement refresh token must survive the next restart."""
    on_refresh_token_update = MagicMock()
    client = WhiskerApiClient(
        MagicMock(),
        "u",
        refresh_token="old-refresh-token",
        on_refresh_token_update=on_refresh_token_update,
    )
    client._user_id = 123
    client._api_key = "api-key"
    client._auth.refresh_tokens = AsyncMock(
        return_value={
            "AccessToken": "new-access-token",
            "RefreshToken": "rotated-refresh-token",
            "ExpiresIn": 3600,
        }
    )

    assert await client._ensure_token() == "new-access-token"
    assert client.refresh_token == "rotated-refresh-token"
    on_refresh_token_update.assert_called_once_with("rotated-refresh-token")


async def test_valid_access_token_hydrates_missing_account_attributes():
    client = WhiskerApiClient(MagicMock(), "u", refresh_token="stored-refresh-token")
    client._access_token = "current-access-token"
    client._token_expiry = dt_util.utcnow() + timedelta(hours=1)
    client._auth.refresh_tokens = AsyncMock()
    client._auth.get_user = AsyncMock(
        return_value={
            "UserAttributes": [
                {"Name": "custom:user_id", "Value": "456"},
                {"Name": "custom:api_key", "Value": "another-api-key"},
            ]
        }
    )

    assert await client._ensure_token() == "current-access-token"
    assert client.user_id == 456
    assert client.api_key == "another-api-key"
    client._auth.refresh_tokens.assert_not_awaited()
    client._auth.get_user.assert_awaited_once_with("current-access-token")


async def test_invalid_refresh_only_token_surfaces_auth_error():
    client = WhiskerApiClient(MagicMock(), "u", refresh_token="expired")
    client._auth.refresh_tokens = AsyncMock(
        side_effect=AuthenticationError("refresh token expired")
    )
    client._auth.authenticate = AsyncMock()

    with pytest.raises(WhiskerAuthError, match="refresh token expired"):
        await client._ensure_token()

    client._auth.authenticate.assert_not_awaited()


async def test_cognito_outage_during_refresh_surfaces_connection_error():
    """A temporary Cognito failure must retry setup instead of forcing reauth."""
    client = WhiskerApiClient(MagicMock(), "u", refresh_token="still-valid")
    client._auth.refresh_tokens = AsyncMock(
        side_effect=CognitoServiceError("Token refresh failed (HTTP 503)")
    )

    with pytest.raises(WhiskerConnectionError, match="refreshing token"):
        await client._ensure_token()


async def test_401_renews_refresh_only_token_and_retries_request():
    """A rejected access token can recover without a stored password."""
    session = MagicMock()
    first_response = MagicMock(status=401)
    retry_response = MagicMock(status=200)
    retry_response.json = AsyncMock(return_value={"ok": True})
    first_context = MagicMock()
    first_context.__aenter__ = AsyncMock(return_value=first_response)
    retry_context = MagicMock()
    retry_context.__aenter__ = AsyncMock(return_value=retry_response)
    contexts = iter((first_context, retry_context))
    request_headers: list[dict[str, str]] = []

    def request(*args, **kwargs):
        request_headers.append(dict(kwargs["headers"]))
        return next(contexts)

    session.request.side_effect = request
    client = WhiskerApiClient(session, "u", refresh_token="stored-refresh-token")
    client._access_token = "rejected-access-token"
    client._token_expiry = dt_util.utcnow() + timedelta(hours=1)
    client._user_id = 123
    client._api_key = "api-key"
    client._auth.refresh_tokens = AsyncMock(
        return_value={"AccessToken": "renewed-access-token", "ExpiresIn": 3600}
    )
    client._auth.authenticate = AsyncMock()

    assert await client._request("GET", "/test") == {"ok": True}
    assert request_headers[0]["Authorization"] == "Bearer rejected-access-token"
    assert request_headers[1]["Authorization"] == "Bearer renewed-access-token"
    assert request_headers[1]["x-wl-api-key"] == "api-key"
    client._auth.refresh_tokens.assert_awaited_once_with("stored-refresh-token")
    client._auth.authenticate.assert_not_awaited()


async def test_invalid_refresh_token_falls_back_to_available_password():
    client = WhiskerApiClient(MagicMock(), "u", password="p", refresh_token="expired")
    client._auth.refresh_tokens = AsyncMock(
        side_effect=AuthenticationError("refresh token expired")
    )
    client._auth.authenticate = AsyncMock(
        return_value={
            "access_token": "password-access-token",
            "id_token": "id-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "user_attributes": [
                {"Name": "custom:user_id", "Value": "123"},
                {"Name": "custom:api_key", "Value": "api-key"},
            ],
        }
    )

    assert await client._ensure_token() == "password-access-token"
    assert client.refresh_token == "replacement-refresh-token"
    client._auth.authenticate.assert_awaited_once_with("u", "p")


async def test_refresh_only_attribute_lookup_error_surfaces_auth_error():
    client = WhiskerApiClient(MagicMock(), "u", refresh_token="stored")
    client._auth.refresh_tokens = AsyncMock(
        return_value={"AccessToken": "access", "ExpiresIn": 3600}
    )
    client._auth.get_user = AsyncMock(
        side_effect=AuthenticationError("access token rejected")
    )

    with pytest.raises(WhiskerAuthError, match="access token rejected"):
        await client._ensure_token()


async def test_missing_required_user_attributes_is_auth_error():
    client = WhiskerApiClient(MagicMock(), "u", refresh_token="stored")
    client._auth.refresh_tokens = AsyncMock(
        return_value={"AccessToken": "access", "ExpiresIn": 3600}
    )
    client._auth.get_user = AsyncMock(return_value={"UserAttributes": []})

    with pytest.raises(WhiskerAuthError, match="required account attributes"):
        await client._ensure_token()
