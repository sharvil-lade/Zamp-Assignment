"""REST API for the React frontend.

One router per domain. Every route returns JSON; none of them render a template
and none of them contain SQL — persistence stays behind `store.py` and business
behaviour stays in `pipeline` / `rules` / `ai_employee`.

Authentication is one shared password: `POST /login` exchanges it for a signed
session token and every other route reads that from the `Authorization` header.
There is no user directory, no session store and nothing to keep server-side.
"""

from fastapi import APIRouter

from . import auth as auth_routes
from . import dashboard, forms as form_routes, onboardings, runs, vendor

router = APIRouter(prefix="/api")

router.include_router(auth_routes.router)
router.include_router(dashboard.router)
router.include_router(onboardings.router)
router.include_router(form_routes.router)
router.include_router(runs.router)
router.include_router(vendor.router)

__all__ = ["router"]
