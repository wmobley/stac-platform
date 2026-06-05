"""Per-user Tapis auth for STAC write (Transactions) routes.

A write is allowed when the caller presents a **valid Tapis access token**
(any user in the tenant). We validate by calling the tenant's userinfo endpoint
(``GET {TAPIS_BASE_URL}/v3/oauth2/userinfo`` with the bearer); Tapis checks the
signature/expiry, so we don't have to. Valid tokens are cached briefly to avoid a
Tapis round-trip on every request.

This replaces the old shared ``STAC_WRITE_TOKEN`` secret — writes are now
attributable to a Tapis user, consistent with the CKAN side (which already uses
the user's Tapis token).
"""

from __future__ import annotations

import base64
import json
import time

import httpx

from . import settings as cfg

# token -> (username, expires_at_monotonic). Short TTL: a token revoked upstream
# stays accepted here for at most _TTL seconds.
_cache: dict[str, tuple[str, float]] = {}
_TTL = 300.0


def username_from_token(token: str) -> str | None:
    """Best-effort username from the (unverified) JWT claims — for logging only."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    return claims.get("tapis/username") or claims.get("username") or claims.get("sub")


def validate_tapis_token(token: str) -> str | None:
    """Return the username if ``token`` is a valid Tapis token, else None."""
    if not token:
        return None
    hit = _cache.get(token)
    now = time.monotonic()
    if hit and hit[1] > now:
        return hit[0]
    try:
        resp = httpx.get(
            f"{cfg.TAPIS_BASE_URL}/v3/oauth2/userinfo",
            headers={"X-Tapis-Token": token},
            timeout=10.0,
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    result = (resp.json() or {}).get("result") or {}
    username = result.get("username") or username_from_token(token) or "tapis-user"
    _cache[token] = (username, now + _TTL)
    return username


def bearer_from_header(authorization: str | None) -> str | None:
    """Extract the bearer token from an Authorization header value."""
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None
