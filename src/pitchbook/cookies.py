"""Chrome cookie extraction for PitchBook session authentication."""

from __future__ import annotations

import logging
import platform
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PITCHBOOK_DOMAIN = ".pitchbook.com"


class CookieExtractionError(Exception):
    """Raised when cookies cannot be extracted from Chrome."""


def _chrome_profile_dirs() -> list[Path]:
    """Return all Chrome profile directories on this system."""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    elif system == "Linux":
        base = Path.home() / ".config" / "google-chrome"
    elif system == "Windows":
        base = Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    else:
        return []

    if not base.exists():
        return []

    profiles: list[Path] = []
    default = base / "Default"
    if default.exists():
        profiles.append(default)
    for p in sorted(base.iterdir()):
        if p.name.startswith("Profile ") and p.is_dir():
            profiles.append(p)
    return profiles


def extract_pitchbook_cookies(chrome_profile: str = "") -> dict[str, str]:
    """Extract PitchBook session cookies from Chrome.

    Args:
        chrome_profile: Chrome profile name (e.g. 'Profile 1' or 'Default').
            If empty, searches all profiles and uses the first one with cookies.

    Returns a dict of cookie name -> value for pitchbook.com domain.

    Raises:
        CookieExtractionError: If browser_cookie3 is not installed or Chrome
            cookies cannot be accessed.
    """
    try:
        import browser_cookie3  # type: ignore[import-untyped,import-not-found,unused-ignore]
    except ImportError as exc:
        raise CookieExtractionError(
            "browser_cookie3 is required for cookie-based auth. "
            "Install it with: pip install browser-cookie3"
        ) from exc

    profiles = _chrome_profile_dirs()

    if chrome_profile:
        # Use specific profile
        profile_dir = None
        for p in profiles:
            if p.name == chrome_profile:
                profile_dir = p
                break
        if profile_dir is None:
            available = [p.name for p in profiles]
            raise CookieExtractionError(
                f"Chrome profile '{chrome_profile}' not found. "
                f"Available profiles: {', '.join(available) or 'none'}"
            )
        cookies = _extract_from_profile(browser_cookie3, profile_dir)
        if not cookies:
            raise CookieExtractionError(
                f"No PitchBook cookies found in Chrome profile '{chrome_profile}'. "
                "Make sure you are logged into pitchbook.com in that profile."
            )
        return cookies

    # Search all profiles for PitchBook cookies
    for profile_dir in profiles:
        try:
            cookies = _extract_from_profile(browser_cookie3, profile_dir)
            if cookies:
                logger.info(
                    "Found PitchBook cookies in Chrome profile: %s", profile_dir.name
                )
                return cookies
        except Exception:
            logger.debug("Skipping profile %s (access error)", profile_dir.name)
            continue

    raise CookieExtractionError(
        f"No cookies found for {PITCHBOOK_DOMAIN} in any Chrome profile. "
        "Make sure you are logged into pitchbook.com in Chrome."
    )


def _extract_from_profile(
    browser_cookie3: Any, profile_dir: Path
) -> dict[str, str]:
    """Extract PitchBook cookies from a specific Chrome profile."""
    cookie_file = profile_dir / "Cookies"
    if not cookie_file.exists():
        return {}

    try:
        cj = browser_cookie3.chrome(
            domain_name="pitchbook.com",
            cookie_file=str(cookie_file),
        )
    except Exception as exc:
        logger.debug("Failed to read cookies from %s: %s", profile_dir.name, exc)
        return {}

    cookies: dict[str, str] = {}
    for cookie in cj:
        cookies[cookie.name] = cookie.value

    logger.debug("Profile %s: %d PitchBook cookies", profile_dir.name, len(cookies))
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
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if "login" in location.lower() or "signin" in location.lower():
                return False
        return resp.status_code < 400
    except httpx.HTTPError:
        return False
