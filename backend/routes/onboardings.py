"""Onboarding cases: create against a form, manage the vendor link.

The form is chosen here, by an authenticated employee, and a snapshot of it is
recorded on the case. A vendor never gets to say which form they are filling in,
and editing the form afterwards does not change what they were asked.

Each case has exactly one vendor URL, stable for its whole life. It is used for
the first submission and for every correction; what changes is only whether the
case is currently accepting one.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from data import store
import auth
from auth import require_session

from ._shared import STATUS_LABELS, format_timestamp, vendor_url

router = APIRouter(prefix="/onboardings", tags=["onboardings"])


class CaseIn(BaseModel):
    vendor_name: str
    contact_name: str = ""
    contact_email: str = ""
    form_id: str | None = None


def _resolve_form(form_id: str | None) -> dict:
    """The chosen form, or the built-in standard one as the default."""
    form = store.get_template(form_id) if form_id else None
    if form is None and form_id:
        raise HTTPException(404, "That form does not exist.")
    form = form or store.standard_form()
    if form is None:
        raise HTTPException(500, "No onboarding form is available.")
    return form


@router.post("", status_code=201)
def create_case(body: CaseIn, request: Request,
                _: None = Depends(require_session)) -> dict:
    """Create a case and mint its one-time vendor link.

    The raw token is returned exactly once, here. Only its hash is persisted, so
    it cannot be recovered from the database or the logs afterwards.
    """
    if not body.vendor_name.strip():
        raise HTTPException(400, "Vendor or company name is required.")

    form = _resolve_form(body.form_id)
    token = store.new_token()
    case_id = store.create_case(
        body.vendor_name.strip(), body.contact_name.strip(),
        body.contact_email.strip(), token, created_by=auth.ACTOR,
        form_id=form["id"], form_schema=form["schema"])

    return {
        "case_id": case_id,
        "vendor_url": vendor_url(request, token),
        "form": {"template_id": form["id"], "template_name": form["name"]},
    }


@router.get("/{case_id}")
def case_detail(case_id: str, request: Request,
                _: None = Depends(require_session)) -> dict:
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(404, "That onboarding case does not exist.")

    form = store.get_template(case["form_id"]) if case.get("form_id") else None
    status = case["run_status"] if case.get("run_status") else case["status"]

    return {
        "case_id": case["id"],
        "vendor_name": case["vendor_name"],
        "contact_name": case["contact_name"],
        "contact_email": case["contact_email"],
        "status": case["status"],
        "status_label": STATUS_LABELS.get(status, status),
        "run_id": case["run_id"],
        "created_at": format_timestamp(case["created_at"]),
        "submitted_at": format_timestamp(case["submitted_at"]),
        "created_by": case["created_by_employee"],
        "form": {
            "template_id": case.get("form_id"),
            # A form can be deleted only while unused, so a missing one here
            # means the case predates form selection rather than a dangling id.
            "template_name": form["name"] if form else "—",
        },
        # One link, stable for the life of the case, shown whenever it is
        # needed. `open` is whether it currently accepts a submission.
        "link": {"url": vendor_url(request, case["token"]),
                 "open": case["status"] == store.AWAITING_VENDOR},
    }


@router.post("/{case_id}/reopen")
def reopen(case_id: str, request: Request,
           _: None = Depends(require_session)) -> dict:
    """Let the vendor submit a correction against this case.

    Re-enables the form behind the case's existing URL. It mints no new link and
    creates no new case: the vendor returns to the same form, prefilled with
    what they sent, and their corrections become a new run on this case.

    Deliberately a human action rather than something the pipeline does when it
    decides PENDING - a decision landing overnight must not silently make a case
    submittable again before anyone has read the findings.
    """
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(404, "That onboarding case does not exist.")
    if case["status"] == store.AWAITING_VENDOR:
        raise HTTPException(409, "This form is already open for submission.")

    latest = store.get_run(case["run_id"]) if case["run_id"] else None
    if latest is None or latest["status"] != "PENDING":
        raise HTTPException(409, "Corrections can only be requested while the "
                                 "decision is Pending.")

    store.reopen_case_for_correction(case_id)
    return {"case_id": case_id,
            "vendor_url": vendor_url(request, case["token"]), "open": True}
