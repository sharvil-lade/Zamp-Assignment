"""Onboarding forms.

A form is a form: create one, edit it, copy it, delete it. There are no
versions — an edit changes the form, and the next onboarding uses it as it
stands. Cases already created are unaffected, because each one keeps a snapshot
of the schema it was created with.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai import extract
from engine import forms as form_domain
from data import store
import auth
from auth import require_session

from ._shared import format_timestamp

router = APIRouter(prefix="/forms", tags=["forms"])


class FormIn(BaseModel):
    name: str
    description: str = ""
    form_schema: dict | None = None


class FormPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    form_schema: dict | None = None


class DuplicateIn(BaseModel):
    name: str


def form_out(form: dict) -> dict:
    return {
        "id": form["id"],
        "name": form["name"],
        "description": form["description"],
        "schema": form["schema"],
        "created_by": form["created_by"],
        "created_at": format_timestamp(form["created_at"]),
        "updated_at": format_timestamp(form.get("updated_at")),
        "field_count": len(form_domain.value_fields(form["schema"])),
        "document_count": len(form_domain.document_fields(form["schema"])),
        "is_standard": form["name"] == form_domain.STANDARD_TEMPLATE_NAME,
    }


@router.get("/templates")
def list_templates(_: None = Depends(require_session)) -> dict:
    return {
        "templates": [form_out(f) for f in store.list_templates()],
        "field_types": list(form_domain.FIELD_TYPES),
        "canonical_fields": sorted(form_domain.CANONICAL_FIELDS),
        # The builder offers these as canonical targets for document fields;
        # sending them keeps the list from being retyped in the frontend.
        "document_types": list(extract.DOC_TYPES),
    }


@router.post("/templates", status_code=201)
def create_template(body: FormIn,
                    _: None = Depends(require_session)) -> dict:
    if not body.name.strip():
        raise HTTPException(400, "A form needs a name.")
    schema = body.form_schema or {"sections": [{"title": "Vendor information",
                                                "fields": []}]}
    # Validated on the way in as well as on edit. Without this a form that
    # cannot be rendered could be created and used for an onboarding straight
    # away — there is no publish step left to catch it later.
    if body.form_schema is not None:
        problems = form_domain.validate_schema(schema)
        if problems:
            raise HTTPException(400, "; ".join(problems))
    form_id = store.create_template(body.name.strip(), body.description.strip(),
                                    auth.ACTOR, schema)
    return {"template_id": form_id}


@router.get("/templates/{form_id}")
def template_detail(form_id: str,
                    _: None = Depends(require_session)) -> dict:
    form = store.get_template(form_id)
    if form is None:
        raise HTTPException(404, "That form does not exist.")
    return {"template": form_out(form)}


@router.put("/templates/{form_id}")
def update_template(form_id: str, body: FormPatch,
                    _: None = Depends(require_session)) -> dict:
    """Edit in place. The schema is validated first, so a form that could not be
    rendered is never stored."""
    if body.form_schema is not None:
        problems = form_domain.validate_schema(body.form_schema)
        if problems:
            raise HTTPException(400, "; ".join(problems))
    if body.name is not None and not body.name.strip():
        raise HTTPException(400, "A form needs a name.")

    updated = store.update_template(
        form_id,
        name=body.name.strip() if body.name is not None else None,
        description=body.description.strip() if body.description is not None else None,
        schema=body.form_schema)
    if not updated:
        raise HTTPException(404, "That form does not exist.")
    return {"ok": True}


@router.post("/templates/{form_id}/duplicate", status_code=201)
def duplicate(form_id: str, body: DuplicateIn,
              _: None = Depends(require_session)) -> dict:
    new_id = store.duplicate_template(form_id, body.name.strip() or "Copy",
                                      auth.ACTOR)
    if new_id is None:
        raise HTTPException(404, "That form does not exist.")
    return {"template_id": new_id}


@router.delete("/templates/{form_id}")
def delete_template(form_id: str,
                    _: None = Depends(require_session)) -> dict:
    """Refused for the built-in form, and for any form an onboarding already
    used — a case must always be able to name where its questions came from."""
    form = store.get_template(form_id)
    if form is None:
        raise HTTPException(404, "That form does not exist.")
    if form["name"] == form_domain.STANDARD_TEMPLATE_NAME:
        raise HTTPException(409, "The standard onboarding form cannot be deleted.")
    if not store.delete_template(form_id):
        raise HTTPException(409, "Onboardings were created from this form, "
                                 "so it cannot be deleted.")
    return {"ok": True}
