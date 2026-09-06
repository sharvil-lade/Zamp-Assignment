"""The vendor portal API. Public, token-only, and deliberately thin.

A vendor receives: the form they were invited to fill in, and confirmation that
it arrived. Never a decision, a finding, a risk level, an AI note, an audit
event, or anything about the employee side.
"""

from ai import extract
from engine import forms as form_domain
from engine import pipeline
from data import store
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/vendor", tags=["vendor"])


def _case_for_token(token: str) -> dict:
    """The only authorisation a vendor has. Case ids are never accepted."""
    case = store.get_case_by_token(token)
    if case is None:
        raise HTTPException(404, "This onboarding link is not valid.")
    return case


def _schema_for(case: dict) -> dict:
    """The snapshot taken when the case was created, so editing the form cannot
    change the questions a vendor is part-way through answering."""
    if case.get("form_schema"):
        return case["form_schema"]
    form = store.standard_form()
    return form["schema"] if form else form_domain.standard_schema()


@router.get("/onboard/{token}")
def vendor_form(token: str) -> dict:
    """Everything React needs to render this vendor's form. Nothing else."""
    case = _case_for_token(token)
    if case["status"] != store.AWAITING_VENDOR:
        return {"state": "submitted", "vendor_name": case["vendor_name"],
                "contact_name": case["contact_name"]}

    schema = _schema_for(case)
    return {
        "state": "open",
        "correcting": case["run_id"] is not None,
        "vendor_name": case["vendor_name"],
        "contact_name": case["contact_name"],
        "schema": schema,
        "prefill": _prefill(case),
        "max_upload_mb": extract.MAX_UPLOAD_BYTES // 1024 // 1024,
        "accepted_types": sorted(extract.MEDIA_TYPES),
    }


def _prefill(case: dict) -> dict:
    """What to put in the form before the vendor starts.

    On a first submission that is only what the employee already knew. On a
    correction round it is everything they sent last time, so they change what
    was wrong instead of retyping fifteen fields — which is also how a
    correction avoids introducing a *new* transcription error.
    """
    base = {"legal_entity_name": case["vendor_name"],
            "contact_name": case["contact_name"] or "",
            "contact_email": case["contact_email"] or ""}
    if case["run_id"] is None:
        return base

    previous = store.get_run(case["run_id"])
    if previous is None:
        return base
    submission = previous["submission"]
    answers = {k: v for k, v in submission.items()
               if k != "_custom" and isinstance(v, str) and v}
    answers.update(submission.get("_custom") or {})
    return {**base, **answers}


@router.post("/onboard/{token}")
async def vendor_submit(token: str, request: Request) -> dict:
    """Accept the submission and hand it to the existing PS-2 pipeline."""
    case = _case_for_token(token)
    if case["status"] != store.AWAITING_VENDOR:
        # One link, one submission: a replayed link cannot start a second run.
        raise HTTPException(409, "This onboarding has already been submitted.")

    schema = _schema_for(case)
    form = await request.form()

    # Canonical fields flow into rules.py exactly as they always have; anything
    # form-specific travels alongside for the Onboarding Assistant and a human.
    raw = {key: form.get(key) for key in form
           if not hasattr(form.get(key), "filename")}
    canonical, custom = form_domain.normalize_submission(schema, raw)
    submission = {**canonical, "_custom": custom}

    run_id = store.create_run(canonical.get("legal_entity_name")
                              or case["vendor_name"], submission)
    store.attach_run_to_case(case["id"], run_id)
    _save_documents(run_id, form, schema)
    store.add_event(run_id, "intake", "vendor_submitted", actor="vendor",
                    detail={"case_id": case["id"],
                            "form_id": case.get("form_id")})

    # Inline, always. A background task does not outlive a serverless
    # response, so deferring the work would silently lose it once deployed.
    pipeline.run(run_id)
    return {"state": "received"}


def _save_documents(run_id: str, form, schema: dict) -> None:
    """Store attachments using the existing validation, unchanged.

    A document field mapped to a canonical PS-2 type is stored under that type
    so the extraction stage finds it exactly where it always has.
    """
    from pathlib import Path

    store.add_event(run_id, "intake", "documents_expected")
    canonical_docs = form_domain.canonical_document_types(schema)

    for field in form_domain.document_fields(schema):
        upload = form.get(field["id"])
        if upload is None or not getattr(upload, "filename", ""):
            continue
        data = upload.file.read(extract.MAX_UPLOAD_BYTES + 1)
        reason = extract.check_upload(upload.filename, data)
        if reason:
            store.add_event(run_id, "intake", "upload_rejected", detail={
                "document": field["id"], "filename": Path(upload.filename).name,
                "reason": reason, "bytes": len(data)})
            continue
        doc_type = canonical_docs.get(field["id"], field["id"])
        store.save_document(run_id, doc_type,
                            Path(upload.filename).suffix.lower(), data)
