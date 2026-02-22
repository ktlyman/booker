"""Tests for Chrome cookie extraction."""

from __future__ import annotations

import time
from types import ModuleType
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pitchbook.cookies import (
    CookieExtractionError,
    cookies_to_httpx,
    extract_pitchbook_cookies,
    validate_cookies,
)


def _mock_rookiepy(cookies: list[dict[str, object]]) -> ModuleType:
    mod = MagicMock(spec=["chrome"])
    mod.chrome.return_value = cookies
    return mod


def test_extract_cookies_no_rookiepy() -> None:
    with patch.dict("sys.modules", {"rookiepy": None}):
        with pytest.raises(CookieExtractionError, match="rookiepy is required"):
            extract_pitchbook_cookies()


def test_extract_cookies_success() -> None:
    mock = _mock_rookiepy([
        {"name": "session_id", "value": "abc123", "expires": int(time.time()) + 3600},
        {"name": "csrftoken", "value": "xyz789", "expires": 0},
    ])
    with patch.dict("sys.modules", {"rookiepy": mock}):
        cookies = extract_pitchbook_cookies()
    assert cookies == {"session_id": "abc123", "csrftoken": "xyz789"}


def test_extract_cookies_filters_expired() -> None:
    mock = _mock_rookiepy([
        {"name": "old", "value": "stale", "expires": int(time.time()) - 3600},
        {"name": "fresh", "value": "good", "expires": int(time.time()) + 3600},
    ])
    with patch.dict("sys.modules", {"rookiepy": mock}):
        cookies = extract_pitchbook_cookies()
    assert cookies == {"fresh": "good"}


def test_extract_cookies_all_expired() -> None:
    mock = _mock_rookiepy([
        {"name": "old", "value": "stale", "expires": int(time.time()) - 3600},
    ])
    with patch.dict("sys.modules", {"rookiepy": mock}):
        with pytest.raises(CookieExtractionError, match="expired"):
            extract_pitchbook_cookies()


def test_extract_cookies_none_found() -> None:
    mock = _mock_rookiepy([])
    with patch.dict("sys.modules", {"rookiepy": mock}):
        with pytest.raises(CookieExtractionError, match="No cookies found"):
            extract_pitchbook_cookies()


def test_extract_cookies_chrome_error() -> None:
    mod = MagicMock(spec=["chrome"])
    mod.chrome.side_effect = OSError("Chrome locked")
    with patch.dict("sys.modules", {"rookiepy": mod}):
        with pytest.raises(CookieExtractionError, match="Failed to extract"):
            extract_pitchbook_cookies()


def test_cookies_to_httpx() -> None:
    jar = cookies_to_httpx({"a": "1", "b": "2"})
    assert isinstance(jar, httpx.Cookies)
    # Verify cookies are stored (httpx.Cookies doesn't have a simple len)
    names = {c.name for c in jar.jar}
    assert names == {"a", "b"}


@pytest.mark.asyncio
async def test_validate_cookies_success() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await validate_cookies(client, "https://example.com") is True


@pytest.mark.asyncio
async def test_validate_cookies_redirect_to_login() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://pitchbook.com/login"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await validate_cookies(client, "https://example.com") is False


@pytest.mark.asyncio
async def test_validate_cookies_http_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await validate_cookies(client, "https://example.com") is False
