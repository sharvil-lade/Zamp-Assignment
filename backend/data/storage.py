"""Document storage: local disk in development, Supabase Storage in production.

The pipeline addresses documents as filesystem paths, so the Supabase backend
materialises objects into a temporary file on read. That keeps `extract.py` and
`pipeline.py` completely untouched by the storage migration — they still receive
a `Path`, exactly as before.

Object keys are built from the case (or run) id and the document type. The
vendor's filename is used for nothing but its extension, which is already
validated by `extract.check_upload`.
"""

import re
import tempfile
from pathlib import Path

import config

PREFIX_RE = re.compile(r"^(onboarding|runs)/(CASE|VS)-\d{4,}$")
DOC_TYPE_RE = re.compile(r"^[a-z_]{3,40}$")
ALLOWED_EXT = frozenset({".pdf", ".png", ".jpg", ".jpeg"})

_LOCAL_ROOT = config.PROJECT_ROOT / "uploads"
_TEMP_ROOT = Path(tempfile.gettempdir()) / "vendor-onboarding-docs"


def object_key(prefix: str, doc_type: str, ext: str) -> str:
    """`onboarding/CASE-0001/bank_proof.pdf` — derived, never vendor-supplied.

    Every component is validated, so a hostile filename, doc type or id cannot
    escape its folder no matter what the caller passes in.
    """
    ext = (ext or "").lower()
    if not PREFIX_RE.match(prefix or ""):
        raise ValueError(f"invalid storage prefix: {prefix!r}")
    if not DOC_TYPE_RE.match(doc_type or ""):
        raise ValueError(f"invalid document type: {doc_type!r}")
    if ext not in ALLOWED_EXT:
        # Second gate. extract.check_upload already refuses anything else, but
        # the storage layer decides for itself what it is willing to hold.
        raise ValueError(f"extension not allowed: {ext!r}")
    return f"{prefix}/{doc_type}{ext}"


# --- local backend ----------------------------------------------------------

def _local_path(key: str) -> Path:
    path = (_LOCAL_ROOT / key).resolve()
    if not str(path).startswith(str(_LOCAL_ROOT.resolve())):
        raise ValueError("resolved outside the upload root")
    return path


def _local_put(key: str, data: bytes) -> None:
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _local_list(prefix: str) -> dict[str, str]:
    folder = _LOCAL_ROOT / prefix
    if not folder.exists():
        return {}
    return {p.stem: f"{prefix}/{p.name}" for p in sorted(folder.iterdir())
            if p.is_file()}


def _local_fetch(key: str) -> Path:
    return _local_path(key)


# --- supabase backend -------------------------------------------------------

def _sb_headers() -> dict:
    # The service-role key is a server-side credential. It is never rendered into
    # a template, never returned in a response, and never logged.
    return {"Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": config.SUPABASE_SERVICE_ROLE_KEY}


def _sb_url(*parts: str) -> str:
    return "/".join([config.SUPABASE_URL, "storage/v1", *parts])


def _sb_put(key: str, data: bytes) -> None:
    import httpx
    r = httpx.post(_sb_url("object", config.SUPABASE_BUCKET, key), content=data,
                   headers={**_sb_headers(), "Content-Type": "application/octet-stream",
                            "x-upsert": "true"}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"storage upload failed ({r.status_code})")


def _sb_list(prefix: str) -> dict[str, str]:
    import httpx
    r = httpx.post(_sb_url("object", "list", config.SUPABASE_BUCKET),
                   json={"prefix": f"{prefix}/", "limit": 100},
                   headers={**_sb_headers(), "Content-Type": "application/json"},
                   timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"storage list failed ({r.status_code})")
    out = {}
    for item in r.json():
        name = item.get("name", "")
        if name and "." in name:
            out[name.rsplit(".", 1)[0]] = f"{prefix}/{name}"
    return out


def _sb_fetch(key: str) -> Path:
    """Download to a temp file so the extraction stage still receives a Path."""
    import httpx
    r = httpx.get(_sb_url("object", config.SUPABASE_BUCKET, key),
                  headers=_sb_headers(), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"storage download failed ({r.status_code})")
    dest = _TEMP_ROOT / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest


# --- public interface -------------------------------------------------------

def put(prefix: str, doc_type: str, ext: str, data: bytes) -> str:
    key = object_key(prefix, doc_type, ext)
    (_sb_put if config.storage_backend() == "supabase" else _local_put)(key, data)
    return key


def list_documents(prefix: str) -> dict[str, str]:
    """{doc_type: object key} for everything stored under this case or run."""
    if not PREFIX_RE.match(prefix or ""):
        raise ValueError(f"invalid storage prefix: {prefix!r}")
    return (_sb_list if config.storage_backend() == "supabase" else _local_list)(prefix)


def fetch(key: str) -> Path:
    """A local Path for the object, downloading it first if it is remote."""
    return (_sb_fetch if config.storage_backend() == "supabase" else _local_fetch)(key)


