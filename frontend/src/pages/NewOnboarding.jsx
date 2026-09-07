import { useState } from "react";
import { Link } from "react-router-dom";
import api from "../api";
import useResource from "../hooks/useResource";
import { ErrorBox, Loading } from "../components/ui";

/**
 * Create a case and mint its invite link.
 *
 * The form is chosen here, by an employee. A vendor never gets to say which
 * form they are filling in — the case records a snapshot of its schema, so a
 * later edit to the form cannot shift it under them.
 */
export default function NewOnboarding() {
  const templates = useResource(() => api.templates(), [], "forms:list");
  const [created, setCreated] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [formId, setFormId] = useState("");

  const forms = templates.data?.templates || [];
  const standard = forms.find((f) => f.is_standard);

  async function onSubmit(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.createCase({
        vendor_name: data.get("vendor_name") || "",
        contact_name: data.get("contact_name") || "",
        contact_email: data.get("contact_email") || "",
        form_id: data.get("form_id") || null,
      }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (created) return <Created created={created} onAnother={() => setCreated(null)} />;

  return (
    <div style={{ maxWidth: 680 }}>
      <h1>New onboarding</h1>
      <p className="faint" style={{ marginBottom: 16 }}>
        Creates a case and its secure vendor link. The vendor fills in the form
        themselves — you never retype their details.
      </p>

      <ErrorBox error={error} />
      {templates.loading && <Loading what="Loading forms" />}

      <form className="card" onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor="vendor_name">Vendor or company name<span className="req">*</span></label>
          <input id="vendor_name" name="vendor_name" required autoFocus />
          <p className="hint">Used to identify the case. The vendor confirms their
            registered legal name on the form.</p>
        </div>

        <div className="grid2">
          <div className="field">
            <label htmlFor="contact_name">Contact name</label>
            <input id="contact_name" name="contact_name" />
          </div>
          <div className="field">
            <label htmlFor="contact_email">Contact email</label>
            <input id="contact_email" name="contact_email" type="email" />
          </div>
        </div>

        <div className="field">
          <label htmlFor="form_id">Form</label>
          <select id="form_id" name="form_id"
                  value={formId || standard?.id || ""}
                  onChange={(e) => setFormId(e.target.value)}>
            {forms.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name} — {f.field_count} fields, {f.document_count} documents
              </option>
            ))}
          </select>
        </div>

        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Creating…" : "Create onboarding link"}
        </button>
      </form>
    </div>
  );
}

/**
 * The link, right after the case is created.
 *
 * It is the case's one permanent URL, not a one-time secret: the case keeps the
 * token so the link can be shown again from the case page, and it is the same
 * URL the vendor returns to for every correction. Only the gate moves.
 */
function Created({ created, onAnother }) {
  const [copied, setCopied] = useState(false);

  return (
    <div style={{ maxWidth: 680 }}>
      <h1>Onboarding created</h1>
      <p className="faint" style={{ marginBottom: 16 }}>
        Send this link to the vendor. They can submit once; if a correction is
        needed, the same link reopens.
      </p>

      <div className="card">
        <h3>Secure vendor link</h3>
        <p className="mono" style={{ wordBreak: "break-all", fontSize: 13 }}>
          {created.vendor_url}
        </p>
        <div className="row" style={{ marginTop: 10 }}>
          <button
            className="primary"
            onClick={async () => {
              await copy(created.vendor_url);
              setCopied(true);
            }}
          >
            {copied ? "Copied" : "Copy link"}
          </button>
          <a className="btn" href={created.vendor_url} target="_blank" rel="noreferrer">
            Open form
          </a>
        </div>
        <div className="alert warn" style={{ marginTop: 16, marginBottom: 0 }}>
          This is the case's permanent link — you can copy it again from the case
          page at any time. It is unique to this vendor, so treat it as a
          credential and send it only to them.
        </div>
      </div>

      <div className="card">
        <h3>Details</h3>
        <dl className="kv">
          <dt>Case</dt>
          <dd className="mono">{created.case_id}</dd>
          <dt>Form</dt>
          <dd>{created.form.template_name}</dd>
        </dl>
      </div>

      <div className="row">
        <Link className="btn" to={`/onboardings/${created.case_id}`}>View case</Link>
        <Link className="btn" to="/dashboard">Dashboard</Link>
        <button onClick={onAnother}>Create another</button>
      </div>
    </div>
  );
}

/** navigator.clipboard is unavailable over plain HTTP on some hosts. */
export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}
