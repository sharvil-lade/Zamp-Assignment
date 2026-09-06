import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../api";
import useResource from "../hooks/useResource";
import { Badge, Empty, ErrorBox, Loading } from "../components/ui";

/**
 * Every onboarding form an employee can send a vendor.
 *
 * The list creates, copies and deletes; the schema itself is edited in the
 * builder each row links to.
 */

export default function FormTemplates() {
  const { data, error, loading, reload } = useResource(
    () => api.templates(), [], "forms:list");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(null);
  const navigate = useNavigate();

  if (loading && !data) return <Loading what="Loading forms" />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;

  const templates = data.templates || [];

  /** A new form is empty, so there is nothing to look at but the editor. */
  async function onCreate(event) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setActionError(null);
    try {
      const created = await api.createTemplate({
        name: fields.get("name") || "",
        description: fields.get("description") || "",
      });
      navigate(`/forms/${created.template_id}`);
    } catch (err) {
      setActionError(err);
      setBusy(false);
    }
  }

  async function onDuplicate(template) {
    const name = window.prompt("Name for the copy", `${template.name} copy`);
    if (!name) return;
    setBusy(true);
    setActionError(null);
    try {
      const created = await api.duplicateTemplate(template.id, name);
      navigate(`/forms/${created.template_id}`);
    } catch (err) {
      setActionError(err);
      setBusy(false);
    }
  }

  /** The server refuses a form an onboarding already used, so show what it
      says rather than guessing which deletes are allowed. */
  async function onDelete(template) {
    if (!window.confirm(`Delete “${template.name}”? This cannot be undone.`)) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.deleteTemplate(template.id);
      reload();
    } catch (err) {
      setActionError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="between" style={{ marginBottom: 16 }}>
        <div>
          <h1>Forms</h1>
          <p className="faint">
            What a vendor is asked for. An edit applies to the next onboarding;
            cases already created keep the form they were sent.
          </p>
        </div>
        <button className="primary" onClick={() => setCreating((on) => !on)}>
          {creating ? "Cancel" : "New form"}
        </button>
      </div>

      <ErrorBox error={actionError} />

      {creating && (
        <form className="card" onSubmit={onCreate}>
          <h2>New form</h2>
          <div className="field">
            <label htmlFor="name">Name<span className="req">*</span></label>
            <input id="name" name="name" required autoFocus />
          </div>
          <div className="field">
            <label htmlFor="description">Description</label>
            <input id="description" name="description" />
            <p className="hint">Starts empty; you add the questions next.</p>
          </div>
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create and edit"}
          </button>
        </form>
      )}

      <section className="card">
        <h2>Forms</h2>
        {templates.length === 0 ? (
          <Empty>No forms yet.</Empty>
        ) : (
          <table className="form-list">
            <thead>
              <tr>
                <th>Form</th>
                <th style={{ textAlign: "right" }}>Fields</th>
                <th style={{ textAlign: "right" }}>Documents</th>
                <th>Created by</th>
                <th>Last updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {templates.map((t) => (
                <tr key={t.id}>
                  <td>
                    <Link to={`/forms/${t.id}`} style={{ fontWeight: 500 }}>
                      {t.name}
                    </Link>
                    {t.is_standard && <> <Badge label="Default" /></>}
                    {t.description && <div className="faint">{t.description}</div>}
                    {t.is_standard && (
                      <div className="faint">
                        Used when a new onboarding does not pick a form.
                      </div>
                    )}
                  </td>
                  <td style={{ textAlign: "right" }}>{t.field_count}</td>
                  <td style={{ textAlign: "right" }}>{t.document_count}</td>
                  <td className="muted">{t.created_by || "—"}</td>
                  <td className="muted">{t.updated_at}</td>
                  <td style={{ textAlign: "right" }}>
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      <Link className="btn" to={`/forms/${t.id}`}>Edit</Link>
                      <button onClick={() => onDuplicate(t)} disabled={busy}>
                        Duplicate
                      </button>
                      {!t.is_standard && (
                        <button className="danger" onClick={() => onDelete(t)}
                                disabled={busy}>
                          Delete
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
