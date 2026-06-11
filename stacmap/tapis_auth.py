"""Mint a Tapis access token (JWT) for CKAN.

ckan.tacc.utexas.edu is fronted by Tapis auth, so a Tapis access token works as a
bearer token against the CKAN Action API (see :class:`stacmap.ckan.CkanClient`).
This mirrors the password-grant flow the deploy scripts already use
(``subside/tapis/register_pods.py``): ``Tapis(base_url, username, password)`` then
``get_tokens()``.

tapipy is imported lazily so the rest of ``stacmap`` (and its tests) never depends
on it — only callers that actually need to mint a token pay for the import.
"""

from __future__ import annotations

import os
from getpass import getpass


def mint_tapis_jwt(base_url: str | None = None,
                   username: str | None = None,
                   password: str | None = None) -> str:
    """Return a Tapis access-token JWT via the password grant.

    Reads ``TAPIS_BASE_URL`` / ``TAPIS_USERNAME`` / ``TAPIS_PASSWORD`` from the
    environment when the arguments are omitted; prompts (no echo) for a missing
    password so it never has to be passed on the command line.
    """
    base_url = (base_url or os.environ.get("TAPIS_BASE_URL") or "").rstrip("/")
    username = username or os.environ.get("TAPIS_USERNAME")
    password = password or os.environ.get("TAPIS_PASSWORD")
    if not base_url:
        raise RuntimeError("TAPIS_BASE_URL is not set")
    if not username:
        raise RuntimeError("TAPIS_USERNAME is not set")
    if not password:
        password = getpass(f"Tapis password for {username}: ")

    try:
        from tapipy.tapis import Tapis
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("tapipy is required to mint a Tapis token "
                           "(pip install tapipy)") from exc

    t = Tapis(base_url=base_url, username=username, password=password)
    t.get_tokens()
    return t.access_token.access_token
