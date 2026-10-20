"""REST client robustness: timeouts, error sanitizing, null-tolerant parsing."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.whisker_ting.api import (
    WhiskerApiClient,
    WhiskerApiError,
    WhiskerConnectionError,
)
from custom_components.whisker_ting.const import API_BASE_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.util.dt as dt_util

URL = f"{API_BASE_URL}/test"


def _authed_client(session) -> WhiskerApiClient:
    """Return a client whose cached token is valid, so no Cognito call runs."""
    client = WhiskerApiClient(session, "u", refresh_token="stored-refresh-token")
    client._access_token = "access-token"
    client._token_expiry = dt_util.utcnow() + timedelta(hours=1)
    client._user_id = 123
    client._api_key = "api-key"
    return client


async def test_request_timeout_is_a_connection_error(
    hass: HomeAssistant, aioclient_mock
):
    """A hung API must surface as a retryable error, not stall the poll."""
    aioclient_mock.get(URL, exc=TimeoutError())
    client = _authed_client(async_get_clientsession(hass))
    with pytest.raises(WhiskerConnectionError, match="timed out"):
        await client._request("GET", "/test")


async def test_request_carries_an_explicit_timeout():
    session = MagicMock()
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"ok": True})
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    session.request.return_value = context
    client = _authed_client(session)

    assert await client._request("GET", "/test") == {"ok": True}
    timeout = session.request.call_args.kwargs["timeout"]
    assert timeout.total == 30


async def test_error_status_does_not_expose_response_body(
    hass: HomeAssistant, aioclient_mock
):
    aioclient_mock.get(URL, status=500, text="<html>secret account detail</html>")
    client = _authed_client(async_get_clientsession(hass))
    with pytest.raises(WhiskerApiError) as err:
        await client._request("GET", "/test")
    assert "500" in str(err.value)
    assert "secret" not in str(err.value)


async def test_invalid_json_is_an_api_error(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(URL, text="not json")
    client = _authed_client(async_get_clientsession(hass))
    with pytest.raises(WhiskerApiError, match="invalid JSON"):
        await client._request("GET", "/test")


def test_parser_tolerates_explicit_nulls():
    """The API sends null for absent nested objects; parsing must survive."""
    client = WhiskerApiClient(MagicMock(), "u", "p")
    user = client._parse_user_data(
        {
            "id": 1,
            "sites": None,
            "devices": [
                {
                    "serialNumber": "TG-0001",
                    "fireHazardStatus": {
                        "efhStatus": None,
                        "ufhStatus": None,
                        "hexColor": None,
                    },
                    "group": None,
                },
                {"serialNumber": "TG-0002", "fireHazardStatus": None},
                "not-a-device",
            ],
        }
    )
    assert [d.serial_number for d in user.devices] == ["TG-0001", "TG-0002"]
    assert user.devices[0].fire_hazard_status.efh_status.level is None
    assert user.devices[1].group_name is None
    assert user.sites == []


async def test_malformed_refresh_response_is_retryable():
    """A 200 from Cognito without tokens is a service fault, not bad creds."""
    client = WhiskerApiClient(MagicMock(), "u", refresh_token="stored-refresh-token")
    client._auth.refresh_tokens = AsyncMock(return_value={"ExpiresIn": 3600})
    with pytest.raises(WhiskerConnectionError):
        await client._ensure_token()
