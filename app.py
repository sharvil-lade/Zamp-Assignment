"""Vendor Onboarding Decision Engine — HTTP layer.

Routes and rendering only. No validation, no rules, no AI.
See docs/07-architecture.md.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import store

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="Vendor Onboarding Decision Engine", lifespan=lifespan)


@app.get("/")
def root():
    # Part 6 replaces this with the submit form.
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request):
    runs = store.list_runs()
    stats = {
        "total": len(runs),
        "approved": sum(r["status"] == "APPROVED" for r in runs),
        "pending": sum(r["status"] == "PENDING" for r in runs),
        "rejected": sum(r["status"] == "REJECTED" for r in runs),
    }
    return templates.TemplateResponse(
        request, "dashboard.html", {"runs": runs, "stats": stats}
    )


@app.post("/reset")
def reset():
    store.reset_all()
    return RedirectResponse("/dashboard", status_code=303)
