"""Dashboard data. Same metrics, same columns, same meaning as before."""

from fastapi import APIRouter, Depends

from data import store
from auth import require_session

from ._shared import STATUS_LABELS, case_row, run_row

router = APIRouter(tags=["dashboard"])

STATUSES = ("APPROVED", "PENDING", "REJECTED", "ERROR", store.AWAITING_VENDOR)


@router.get("/dashboard")
def dashboard(status: str | None = None,
              _: None = Depends(require_session)) -> dict:
    wanted = status.upper() if status else None
    if wanted not in (None, *STATUSES):
        wanted = None

    all_runs = store.list_runs()
    all_cases = [case_row(c) for c in store.list_cases()]
    runs = [r for r in all_runs if r["status"] == wanted] if wanted else all_runs

    # Counted over cases, not runs, because cases are what the table below
    # shows. Counting runs made the tiles disagree with the rows the moment a
    # case had two of them — a correction cycle is one vendor, not two.
    return {
        "stats": {
            "total": len(all_cases),
            "awaiting_vendor": sum(c["status"] == store.AWAITING_VENDOR
                                   for c in all_cases),
            "approved": sum(c["status"] == "APPROVED" for c in all_cases),
            "pending": sum(c["status"] == "PENDING" for c in all_cases),
            "rejected": sum(c["status"] == "REJECTED" for c in all_cases),
            "error": sum(c["status"] == "ERROR" for c in all_cases),
        },
        "cases": all_cases,
        "runs": [run_row(r) for r in runs],
        "statuses": list(STATUSES),
        "status_labels": STATUS_LABELS,
        "active": wanted,
    }
