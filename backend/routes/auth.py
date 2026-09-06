"""Sign in with the shared password, and ask whether this session is still good.

`POST /login` takes one password and returns a session token. Every later
request presents it as `Authorization: Bearer <token>`. There is no logout
endpoint: the token is signed rather than stored, so signing out is the client
discarding it.

There is no sign-up, no user record, no role and no reset flow - by design.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import auth as auth_module

router = APIRouter(tags=["auth"])


class Credentials(BaseModel):
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class Session(BaseModel):
    authenticated: bool


@router.post("/login", response_model=Token)
def login(credentials: Credentials) -> Token:
    if not auth_module.check_password(credentials.password):
        raise HTTPException(401, "That password was not recognised.")
    return Token(access_token=auth_module.issue_token(),
                 expires_in=auth_module.SESSION_TTL_SECONDS)


@router.get("/session", response_model=Session)
def session(request: Request) -> Session:
    """Is this token still valid? React calls it on boot to decide what to
    render, and it is how a stored token is checked for expiry."""
    return Session(authenticated=auth_module.is_signed_in(request))
