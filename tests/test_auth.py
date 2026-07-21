"""Tests for the AWS Cognito SRP auth (auth.py)."""

from __future__ import annotations

import base64
import datetime as dt
from typing import TYPE_CHECKING

from aioresponses import aioresponses
import pytest

from custom_components.whisker_ting.auth import (
    COGNITO_IDP_URL,
    AuthenticationError,
    CognitoSRP,
    WhiskerAuth,
    get_cognito_formatted_timestamp,
    pad_hex,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CHALLENGE = {
    "ChallengeName": "PASSWORD_VERIFIER",
    "ChallengeParameters": {
        "USERNAME": "ada",
        "USER_ID_FOR_SRP": "ada",
        "SALT": "a1b2c3",
        "SRP_B": "deadbeef" * 32,
        "SECRET_BLOCK": base64.b64encode(b"secret-block").decode(),
    },
}
TOKENS = {
    "AuthenticationResult": {
        "AccessToken": "at",
        "IdToken": "it",
        "RefreshToken": "rt",
        "ExpiresIn": 3600,
    }
}
USER = {
    "UserAttributes": [
        {"Name": "custom:user_id", "Value": "12345"},
        {"Name": "custom:api_key", "Value": "fake-api-key"},
    ]
}


def test_pad_hex_even_and_high_bit():
    assert pad_hex("1") == "01"  # odd length -> leading zero
    assert pad_hex("ff").startswith("00")  # high bit set -> 00 prefix


def test_cognito_timestamp_format():
    ts = get_cognito_formatted_timestamp(
        dt.datetime(2026, 7, 21, 9, 5, 3, tzinfo=dt.UTC)
    )
    assert ts == "Tue Jul 21 09:05:03 UTC 2026"


def test_cognito_timestamp_day_not_zero_padded():
    # 2026-07-01 is a Wednesday; the day is formatted with `{day:d}`, not
    # `{day:02d}`, so a single-digit day must NOT come out zero-padded.
    ts = get_cognito_formatted_timestamp(
        dt.datetime(2026, 7, 1, 9, 5, 3, tzinfo=dt.UTC)
    )
    assert ts == "Wed Jul 1 09:05:03 UTC 2026"


def test_srp_key_derivation_deterministic():
    srp = CognitoSRP("ada", "pw", pool_id="us-east-1_abc", client_id="cid")
    params = CHALLENGE["ChallengeParameters"]

    # The HKDF key derivation is a pure function of its inputs (no internal
    # randomness), so calling it twice with identical inputs on the same
    # instance must yield identical key bytes.
    key_a = srp.get_password_authentication_key(
        params["USER_ID_FOR_SRP"], "pw", int(params["SRP_B"], 16), params["SALT"]
    )
    key_b = srp.get_password_authentication_key(
        params["USER_ID_FOR_SRP"], "pw", int(params["SRP_B"], 16), params["SALT"]
    )
    assert key_a == key_b

    result = srp.process_challenge(params, {"USERNAME": "ada"})
    assert set(result) == {
        "TIMESTAMP",
        "USERNAME",
        "PASSWORD_CLAIM_SECRET_BLOCK",
        "PASSWORD_CLAIM_SIGNATURE",
    }
    assert result["PASSWORD_CLAIM_SECRET_BLOCK"] == params["SECRET_BLOCK"]


async def test_authenticate_end_to_end(hass: HomeAssistant):
    with aioresponses() as m:
        m.post(COGNITO_IDP_URL, payload=CHALLENGE)  # InitiateAuth
        m.post(COGNITO_IDP_URL, payload=TOKENS)  # RespondToAuthChallenge
        m.post(COGNITO_IDP_URL, payload=USER)  # GetUser
        auth = WhiskerAuth(async_get_clientsession(hass))
        result = await auth.authenticate("ada", "pw")
    assert result["access_token"] == "at"
    assert {"Name": "custom:api_key", "Value": "fake-api-key"} in result[
        "user_attributes"
    ]


async def test_authenticate_bad_credentials(hass: HomeAssistant):
    with aioresponses() as m:
        m.post(COGNITO_IDP_URL, status=400, body='{"__type":"NotAuthorizedException"}')
        auth = WhiskerAuth(async_get_clientsession(hass))
        with pytest.raises(AuthenticationError):
            await auth.authenticate("ada", "wrong")
