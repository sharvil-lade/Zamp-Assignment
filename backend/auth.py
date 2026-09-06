"""Password-only access to the employee side of the app.

One shared password, read from `APP_PASSWORD`. There is deliberately no user
directory, no sign-up, no roles, no OAuth and no password reset: this is a door,
not an identity system.

Signing in exchanges the password for a session token, carried the same way as
before:

    Authorization: Bearer <expires_at>.<signature>

The token is signed with the password itself using HMAC-SHA256 from the standard
library, so nothing is stored server-side, sessions survive a restart, and
changing `APP_PASSWORD` invalidates every existing session at once. A token
cannot be revoked individually before it expires - see
docs/10-assumptions-and-scope.md.

The vendor portal is untouched by any of this. A vendor authenticates with their
own one-time link token (`store.get_case_by_token`) and never sees this module.
"""

import hashlib
import hmac
import time

from starlette.requests import Request

import config

BEARER = "bearer"

# How long a session lasts. A constant rather than a setting: one shared
# password behind a login has no operator who needs to tune this.
SESSION_TTL_SECONDS = 12 * 60 * 60

# Who a signed-in user is in the audit trail. With a single shared password
# there is genuinely no identity to record, so the trail says what is true - an
# authenticated operator acted - rather than inventing a name. Restoring
# per-person attribution means restoring a user directory.
ACTOR = "user:operator"


class NotAuthenticated(Exception):
    """Raised by the dependency; turned into a 401 by app.py."""


# --- the password -----------------------------------------------------------

def check_password(password: str) -> bool:
    """Constant-time comparison, so a wrong password leaks nothing by timing."""
    return hmac.compare_digest((password or "").encode(),
                               config.APP_PASSWORD.encode())


def _sign(expires_at: int) -> str:
    return hmac.new(config.APP_PASSWORD.encode(), str(expires_at).encode(),
                    hashlib.sha256).hexdigest()


# --- session tokens ---------------------------------------------------------

def issue_token(now: float | None = None) -> str:
    """A signed statement that somebody knew the password, and until when."""
    expires_at = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    return f"{expires_at}.{_sign(expires_at)}"


def valid_token(token: str, now: float | None = None) -> bool:
    """True only for a token this server signed that has not expired."""
    expiry, _, signature = (token or "").partition(".")
    if not expiry.isdigit() or not signature:
        return False
    if not hmac.compare_digest(signature, _sign(int(expiry))):
        return False
    return int(expiry) > (now if now is not None else time.time())


def token_from(request: Request) -> str | None:
    """The Authorization header, and nothing else.

    Never a query parameter: those end up in access logs, browser history and
    referrer headers, which is exactly where a credential must not be.
    """
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == BEARER and value.strip() else None


def is_signed_in(request: Request) -> bool:
    token = token_from(request)
    return bool(token) and valid_token(token)


def require_session(request: Request) -> None:
    """FastAPI dependency for every employee route.

    `request` must stay annotated: without it FastAPI treats the parameter as a
    query field and every protected route answers 422 instead of authenticating.
    """
    if not is_signed_in(request):
        raise NotAuthenticated()
