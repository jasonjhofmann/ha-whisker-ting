"""WebSocket decode + auth-header + rejection tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import msgpack
import pytest

from custom_components.whisker_ting.websocket import WhiskerWebSocket


def voltage_frame(v=120.5, peaks=5.0, hi=121.0, lo=119.0) -> bytes:
    return msgpack.packb(
        {1: [1, {}, "1", "updateComboBinaryData", [[v, peaks, hi, lo]]]},
        use_bin_type=True,
    )


def completion_frame() -> bytes:
    # SignalR Completion (type 3), length-prefixed as the server sends it.
    body = msgpack.packb([3, {}, "1", 2, None], use_bin_type=True)
    return bytes([len(body)]) + body


class _Msg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False
        self.sent_bytes = []

    async def send_str(self, _data):
        return

    async def send_bytes(self, data):
        self.sent_bytes.append(data)

    async def close(self):
        self.closed = True

    async def receive(self, timeout=None):
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(3600)


def _make(frames):
    ws = _FakeWS(frames)
    session = MagicMock()
    session.ws_connect = AsyncMock(return_value=ws)
    return session, ws


async def test_connect_sends_api_key_header():
    handshake = _Msg(aiohttp.WSMsgType.TEXT, "{}\x1e")
    session, ws = _make([handshake, _Msg(aiohttp.WSMsgType.BINARY, voltage_frame())])
    client = WhiskerWebSocket(
        session=session, api_key="secret-key", user_id=12345, station_id="TG-0001",
        on_voltage_update=lambda sid, data: None,
    )
    assert await client.connect() is True
    headers = session.ws_connect.call_args.kwargs["headers"]
    assert headers["x-wl-api-key"] == "secret-key"
    await client.disconnect()


async def test_decode_voltage():
    handshake = _Msg(aiohttp.WSMsgType.TEXT, "{}\x1e")
    frame = _Msg(aiohttp.WSMsgType.BINARY, voltage_frame())
    session, ws = _make([handshake, frame])
    seen = []
    client = WhiskerWebSocket(
        session=session, api_key="k", user_id=1, station_id="TG-0001",
        on_voltage_update=lambda sid, data: seen.append((sid, data)),
    )
    assert await client.connect() is True
    assert await client.wait_for_data(timeout=1.0) is True
    await client.disconnect()
    assert seen and seen[0][0] == "TG-0001"
    assert seen[0][1].voltage == 120.5
    assert seen[0][1].voltage_hi == 121.0


async def test_rejection_fast_fail():
    handshake = _Msg(aiohttp.WSMsgType.TEXT, "{}\x1e")
    rej = _Msg(aiohttp.WSMsgType.BINARY, completion_frame())
    session, ws = _make([handshake, rej])
    client = WhiskerWebSocket(
        session=session, api_key="k", user_id=1, station_id="TG-0001",
        on_voltage_update=lambda sid, data: None,
    )
    assert await client.connect() is True
    assert await client.wait_for_data(timeout=2.0) is False
    await client.disconnect()
