"""API authentication and security — CLAUDE.md rule 14.

Security rules:
  - Bind only to localhost (enforced at uvicorn startup; not here).
  - A per-session token is generated RANDOMLY at each process start and never
    logged. Every state-changing endpoint requires it in X-Session-Token.
  - Check BOTH the Origin and Host headers on state-changing requests, and
    reject anything not from localhost.
"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Header, HTTPException, Request, status

# A fresh random token every run (rule 14). The server embeds this in the
# served dashboard HTML; it is never read from the environment or logged.
_SESSION_TOKEN: str = secrets.token_hex(32)

_LOCAL_PREFIXES = ("http://localhost", "http://127.0.0.1")
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def get_session_token() -> str:
    """Return the current session token (for embedding in the dashboard)."""
    return _SESSION_TOKEN


def _allowed_origin(origin: Optional[str]) -> bool:
    if origin is None:
        return True  # same-origin requests carry no Origin header
    return origin.startswith(_LOCAL_PREFIXES)


def _allowed_host(host: Optional[str]) -> bool:
    if host is None:
        return False  # a state-changing request must declare its Host
    hostname = host.split(":", 1)[0].lower()
    return hostname in _LOCAL_HOSTS


async def require_token(
    request: Request,
    x_session_token: Optional[str] = Header(default=None),
) -> None:
    """Dependency enforcing the session token + Origin + Host checks."""
    if not _allowed_origin(request.headers.get("origin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin requests are not permitted.",
        )
    if not _allowed_host(request.headers.get("host")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requests must target localhost.",
        )
    if not x_session_token or not secrets.compare_digest(x_session_token, _SESSION_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid session token.",
        )
