"""Tests for Chrome cookie extraction."""

from __future__ import annotations

from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pitchbook.cookies import (
    CookieExtractionError,
    cookies_to_httpx,
    extract_pitchbook_cookies,
    validate_cookies,
)


def _make_cookie(name: str, value: str) -> Cookie:
    """Create a minimal http.cookiejar.Cookie for testing."""
    return Cookie(
        version=0, name=name, value=value,
        port=None, port_specified=False,
        domain=".pitchbook.com", domain_specified=True, domain_initial_dot=True,
        path="/", path_specified=True,
        secure=True, expires=None, discard=True,
        comment=None, comment_url=None, rest={},
    )


def _mock_browser_cookie3(cookies: list[Cookie]) -> MagicMock:
    """Create a mock browser_cookie3 module that returns the given cookies."""
    mod = MagicMock()
    mod.chrome.return_value = cookies
    return mod


@pytest.fixture()
def _fake_profiles(tmp_path: Path) -> list[Path]:
    """Create fake Chrome profile dirs with Cookies files."""
    profiles = []
    for name in ["Default", "Profile 1"]:
        d = tmp_path / name
        d.mkdir()
        (d / "Cookies").touch()
        profiles.append(d)
    return profiles


def test_extract_cookies_no_browser_cookie3() -> None:
    with patch.dict("sys.modules", {"browser_cookie3": None}):
        with pytest.raises(CookieExtractionError, match="browser_cookie3 is required"):
            extract_pitchbook_cookies()


def test_extract_cookies_success() -> None:
    mock = _mock_browser_cookie3([
        _make_cookie("session_id", "abc123"),
        _make_cookie("csrftoken", "xyz789"),
    ])
    with (
        patch.dict("sys.modules", {"browser_cookie3": mock}),
        patch("pitchbook.cookies._chrome_profile_dirs") as mock_dirs,
    ):
        fake_dir = Path("/tmp/test-chrome/Default")
        mock_dirs.return_value = [fake_dir]
        # Create fake Cookies file
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "Cookies").touch()
        cookies = extract_pitchbook_cookies()
    assert cookies == {"session_id": "abc123", "csrftoken": "xyz789"}


def test_extract_cookies_none_found() -> None:
    mock = _mock_browser_cookie3([])
    with (
        patch.dict("sys.modules", {"browser_cookie3": mock}),
        patch("pitchbook.cookies._chrome_profile_dirs") as mock_dirs,
    ):
        fake_dir = Path("/tmp/test-chrome-empty/Default")
        mock_dirs.return_value = [fake_dir]
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "Cookies").touch()
        with pytest.raises(CookieExtractionError, match="No cookies found"):
            extract_pitchbook_cookies()


def test_extract_cookies_specific_profile_not_found() -> None:
    mock = _mock_browser_cookie3([])
    with (
        patch.dict("sys.modules", {"browser_cookie3": mock}),
        patch("pitchbook.cookies._chrome_profile_dirs") as mock_dirs,
    ):
        mock_dirs.return_value = []
        with pytest.raises(CookieExtractionError, match="not found"):
            extract_pitchbook_cookies("NonExistent")


def test_cookies_to_httpx() -> None:
    jar = cookies_to_httpx({"a": "1", "b": "2"})
    assert isinstance(jar, httpx.Cookies)
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
