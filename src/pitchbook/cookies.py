"""Chrome cookie extraction for PitchBook session authentication."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PITCHBOOK_DOMAIN = ".pitchbook.com"


class CookieExtractionError(Exception):
    """Raised when cookies cannot be extracted from Chrome."""


def extract_pitchbook_cookies() -> dict[str, str]:
    """Extract PitchBook session cookies from Chrome.

    Returns a dict of cookie name -> value for pitchbook.com domain.

    Raises:
        CookieExtractionError: If rookiepy is not installed or Chrome
            cookies cannot be accessed.
    """
    try:
        import rookiepy  # type: ignore[import-untyped,import-not-found,unused-ignore]
    except ImportError as exc:
        raise CookieExtractionError(
            "rookiepy is required for cookie-based auth. "
            "Install it with: pip install rookiepy"
        ) from exc

    try:
        raw_cookies: list[dict[str, Any]] = rookiepy.chrome(domains=[PITCHBOOK_DOMAIN])
    except Exception as exc:
        raise CookieExtractionError(
            f"Failed to extract Chrome cookies for {PITCHBOOK_DOMAIN}: {exc}"
        ) from exc

    if not raw_cookies:
        raise CookieExtractionError(
            f"No cookies found for {PITCHBOOK_DOMAIN} in Chrome. "
            "Make sure you are logged into pitchbook.com in Chrome."
        )

    now = time.time()
    cookies: dict[str, str] = {}
    for cookie in raw_cookies:
        expires = cookie.get("expires", 0)
        # expires=0 means session cookie (valid until browser close)
        if expires != 0 and expires < now:
            continue
        cookies[cookie["name"]] = cookie["value"]

    if not cookies:
        raise CookieExtractionError(
            "All PitchBook cookies have expired. "
            "Please log into pitchbook.com in Chrome and try again."
        )

    logger.info("Extracted %d PitchBook cookies from Chrome", len(cookies))
    return cookies


def cookies_to_httpx(cookie_dict: dict[str, str]) -> httpx.Cookies:
    """Convert a cookie dict to an httpx.Cookies object."""
    jar = httpx.Cookies()
    for name, value in cookie_dict.items():
        jar.set(name, value, domain=PITCHBOOK_DOMAIN)
    return jar


async def validate_cookies(
    http_client: httpx.AsyncClient,
    validation_url: str = "https://pitchbook.com",
) -> bool:
    """Make a lightweight request to verify cookies are still valid.

    Returns True if the cookies authenticate successfully (non-redirect to login).
    """
    try:
        resp = await http_client.get(validation_url, follow_redirects=False)
        # A redirect to a login page indicates expired cookies
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if "login" in location.lower() or "signin" in location.lower():
                return False
        return resp.status_code < 400
    except httpx.HTTPError:
        return False
