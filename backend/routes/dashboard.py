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
    cases = all_cases

    return {
        "stats": {
            "total": len(all_runs),
            "approved": sum(r["status"] == "APPROVED" for r in all_runs),
            "pending": sum(r["status"] == "PENDING" for r in all_runs),
            "rejected": sum(r["status"] == "REJECTED" for r in all_runs),
            "error": sum(r["status"] == "ERROR" for r in all_runs),
        },
        "cases": cases,
        "runs": [run_row(r) for r in runs],
        "statuses": list(STATUSES),
        "status_labels": STATUS_LABELS,
        "active": wanted,
    }
