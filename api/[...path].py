"""Vercel serverless entrypoint.

Vercel discovers functions under `api/`, so this file exists only to put the
backend package on the import path and hand Vercel the same ASGI app that
`uvicorn app:app --app-dir backend` runs locally. There is no second
application and no Vercel-specific behaviour: whatever is true here is true on
a laptop.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from app import app  # noqa: E402  (the path has to be set first)

__all__ = ["app"]
