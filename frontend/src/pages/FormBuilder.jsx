import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import api from "../api";
import useResource from "../hooks/useResource";
import SchemaForm from "../components/SchemaForm";
import { ErrorBox, Loading } from "../components/ui";

/**
 * Build one onboarding form.
 *
 * Two tabs, because those are the only two questions an author has: what am I
 * asking, and what will they see? Editing is one card per question with just a
 * label and a type on show — everything else appears on the card you are
 * actually working on, and nowhere else.
 *
 * A save changes the form in place, and the next onboarding uses it as it
 * stands. Cases already created are unaffected: each keeps its own snapshot of
 * the schema it was built from, so what a vendor was asked never changes after
 * the fact.
 *
 * The editor holds a single `schema` object in state and rewrites it immutably.
 * Client-side checks mirror `forms.validate_schema` for immediate feedback, but
 * the server validates again and its message is what is shown.
 */

const FIELD_ID_RE = /^[a-z][a-z0-9_]{0,60}$/;

export default function FormBuilder() {
  const { templateId } = useParams();

  const detail = useResource(() => api.template(templateId), [templateId],
                             `forms:${templateId}`);
  // field types + canonical names
  const meta = useResource(() => api.templates(), [], "forms:list");

  const [tab, setTab] = useState("edit");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [schema, setSchema] = useState(null);
  const [selected, setSelected] = useState(null);   // "sectionIndex:fieldIndex"
  const [saveError, setSaveError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  const template = detail.data?.template || null;

  // Keyed on the id, not the payload: a reload after saving must not throw away
  // edits made while the request was in flight.
  useEffect(() => {
    if (!template) return;
    setName(template.name);
    setDescription(template.description || "");
    setSchema(template.schema);
  }, [template?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (detail.loading && !detail.data) return <Loading what="Loading the form" />;
  if (detail.error) return <ErrorBox error={detail.error} onRetry={detail.reload} />;
  if (!template || !schema) return null;

  const problems = schemaProblems(schema);
  // Both lists come from the server — rules.SUBMISSION_FIELDS and
  // extract.DOC_TYPES — so the builder cannot offer a canonical that does not exist.
  const canonicals = [...(meta.data?.canonical_fields || []),
                      ...(meta.data?.document_types || [])];
  const fieldTypes = meta.data?.field_types || ["text"];

  async function save() {
    setBusy(true);
    setSaveError(null);
    setNotice(null);
    try {
      await api.updateTemplate(templateId, { name, description, form_schema: schema });
      setNotice("Saved.");
      detail.reload();
    } catch (err) {
      setSaveError(err);
    } finally {
      setBusy(false);
    }
  }

  // --- immutable schema edits ------------------------------------------------

  const patchSections = (fn) =>
    setSchema((s) => ({ ...s, sections: fn(s.sections || []) }));
  const patchSection = (i, patch) =>
    patchSections((secs) => secs.map((sec, n) => (n === i ? { ...sec, ...patch } : sec)));
  const patchFields = (i, fn) =>
    patchSections((secs) =>
      secs.map((sec, n) => (n === i ? { ...sec, fields: fn(sec.fields || []) } : sec)));
  const patchField = (i, j, patch) =>
    patchFields(i, (fields) => fields.map((f, n) => (n === j ? { ...f, ...patch } : f)));

  const addField = (i) => {
    const fields = schema.sections[i].fields || [];
    patchFields(i, (f) => [...f, { id: "", label: "", type: "text", required: false }]);
    setSelected(`${i}:${fields.length}`);
  };

  return (
    <>
      <div className="between" style={{ marginBottom: 16 }}>
        <div style={{ flex: 1, marginRight: 16 }}>
          <p className="faint" style={{ marginBottom: 2 }}>
            <Link to="/forms">All forms</Link>
          </p>
          <input
            className="plain big"
            aria-label="Form name"
            value={name}
            placeholder="Untitled form"
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <button className="primary" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "edit"} onClick={() => setTab("edit")}>
          Edit
        </button>
        <button role="tab" aria-selected={tab === "preview"}
                onClick={() => setTab("preview")}>
          Form view
        </button>
      </div>

      <ErrorBox error={saveError} />
      {notice && <div className="alert ok">{notice}</div>}

      {tab === "preview" ? (
        <Preview name={name} description={description} schema={schema} />
      ) : (
        <>
          <div className="card">
            <input
              className="plain"
              aria-label="Form description"
              value={description}
              placeholder="Add a description for the vendor"
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {problems.length > 0 && (
            <div className="alert warn">
              <strong>This form cannot be saved yet:</strong>
              <ul style={{ margin: "6px 0 0 18px", padding: 0 }}>
                {problems.map((problem) => <li key={problem}>{problem}</li>)}
              </ul>
            </div>
          )}

          {(schema.sections || []).map((section, i) => (
            <section key={i} style={{ marginBottom: 16 }}>
              <div className="row" style={{ alignItems: "flex-end", marginBottom: 10 }}>
                <div style={{ flex: 1 }}>
                  <input
                    className="plain"
                    aria-label={`Section ${i + 1} title`}
                    value={section.title || ""}
                    placeholder="Section title"
                    onChange={(e) => patchSection(i, { title: e.target.value })}
                  />
                  <input
                    className="plain faint"
                    aria-label={`Section ${i + 1} description`}
                    value={section.description || ""}
                    placeholder="Section description (optional)"
                    onChange={(e) =>
                      patchSection(i, { description: e.target.value || undefined })}
                  />
                </div>
                <button className="icon" title="Move section up"
                        onClick={() => patchSections((s) => moved(s, i, i - 1))}>↑</button>
                <button className="icon" title="Move section down"
                        onClick={() => patchSections((s) => moved(s, i, i + 1))}>↓</button>
                <button className="icon danger" title="Remove section"
                        onClick={() => patchSections((s) => s.filter((_, n) => n !== i))}>
                  Remove section
                </button>
              </div>

              {(section.fields || []).map((field, j) => (
                <FieldCard
                  key={j}
                  field={field}
                  open={selected === `${i}:${j}`}
                  onOpen={() => setSelected(`${i}:${j}`)}
                  idPrefix={`sec-${i}-f-${j}`}
                  fieldTypes={fieldTypes}
                  canonicals={canonicals}
                  onChange={(patch) => patchField(i, j, patch)}
                  onMove={(delta) => patchFields(i, (f) => moved(f, j, j + delta))}
                  onDuplicate={() =>
                    patchFields(i, (f) => [...f.slice(0, j + 1),
                                           { ...f[j], id: "" }, ...f.slice(j + 1)])}
                  onRemove={() => patchFields(i, (f) => f.filter((_, n) => n !== j))}
                />
              ))}

              <button onClick={() => addField(i)}>Add field</button>
            </section>
          ))}

          <button
            onClick={() => patchSections((s) => [...s, { title: "New section", fields: [] }])}
          >
            Add section
          </button>
        </>
      )}
    </>
  );
}

/** Exactly what the vendor portal renders, from the same component it uses. */
function Preview({ name, description, schema }) {
  return (
    <>
      <p className="faint" style={{ marginBottom: 16 }}>
        This is the form as a vendor sees it. Nothing here is submitted.
      </p>
      <h1>{name || "Untitled form"}</h1>
      {description && <p className="muted" style={{ marginBottom: 16 }}>{description}</p>}
      <SchemaForm schema={schema} />
    </>
  );
}

/**
 * One question. Closed it shows its label and type; open it shows everything
 * the schema allows and nothing it does not.
 */
function FieldCard({ field, open, onOpen, idPrefix, fieldTypes, canonicals,
                     onChange, onMove, onDuplicate, onRemove }) {
  if (!open) {
    return (
      <div className="qcard" onClick={onOpen} role="button" tabIndex={0}
           onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onOpen()}>
        <div className="summary">
          <span style={{ flex: 1, fontWeight: 500 }}>
            {field.label || <span className="faint">Untitled field</span>}
            {field.required && <span className="req"> *</span>}
          </span>
          <span className="kind">{field.type}</span>
          {field.canonical && <span className="badge ai">{field.canonical}</span>}
        </div>
      </div>
    );
  }

  return (
    <div className="qcard on">
      <div className="grid2">
        <div className="field">
          <label htmlFor={`${idPrefix}-label`}>Question</label>
          <input
            id={`${idPrefix}-label`}
            value={field.label || ""}
            placeholder="What are you asking the vendor?"
            onChange={(e) => onChange(withDerivedId(field, e.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor={`${idPrefix}-type`}>Answer type</label>
          <select
            id={`${idPrefix}-type`}
            value={field.type || "text"}
            onChange={(e) => onChange({ type: e.target.value })}
          >
            {fieldTypes.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
        </div>
      </div>

      {field.type === "select" && (
        <div className="field">
          <label htmlFor={`${idPrefix}-options`}>Options</label>
          <input
            id={`${idPrefix}-options`}
            value={(field.options || []).join(", ")}
            onChange={(e) =>
              onChange({ options: e.target.value.split(",").map((o) => o.trim()) })}
            onBlur={() => onChange({ options: (field.options || []).filter(Boolean) })}
          />
          <p className="hint">Comma separated. Anything outside this list is rejected.</p>
        </div>
      )}

      <div className="field">
        <label htmlFor={`${idPrefix}-canonical`}>Checked by the rule engine as</label>
        <select
          id={`${idPrefix}-canonical`}
          value={field.canonical || ""}
          onChange={(e) => onChange({ canonical: e.target.value || undefined })}
        >
          <option value="">nothing — custom field</option>
          {canonicals.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <p className="hint">
          {field.canonical
            ? `Read by the rule engine as “${field.canonical}”, with the existing deterministic checks.`
            : "Custom field: completeness and type checks plus AI review. No business rules."}
        </p>
      </div>

      <details className="note">
        <summary>Canonical fields versus custom fields</summary>
        <p className="muted">
          A field mapped to a canonical name <em>is</em> the field the deterministic
          rule engine reads — GSTIN checksum, IFSC format, bank-proof matching, all
          of it applies unchanged. A field with no mapping gets required, type and
          option checks plus the Onboarding Assistant’s review, and nothing else. That is the
          difference between validated by a rule and read by a human, so choose it
          deliberately.
        </p>
      </details>

      <details className="note" style={{ marginTop: 10 }}>
        <summary>Advanced</summary>
        <div className="field">
          <label htmlFor={`${idPrefix}-id`}>Field id</label>
          <input
            id={`${idPrefix}-id`}
            className="mono"
            value={field.id || ""}
            onChange={(e) => onChange({ id: e.target.value })}
          />
          <p className="hint">
            The key the answer arrives under. Written for you from the question;
            change it only if you have a reason to.
          </p>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`${idPrefix}-help`}>Help text</label>
          <input
            id={`${idPrefix}-help`}
            value={field.help || ""}
            onChange={(e) => onChange({ help: e.target.value || undefined })}
          />
        </div>
      </details>

      <div className="qfoot">
        <div className="checkbox spacer">
          <input
            id={`${idPrefix}-required`}
            type="checkbox"
            checked={Boolean(field.required)}
            onChange={(e) => onChange({ required: e.target.checked })}
          />
          <label htmlFor={`${idPrefix}-required`} style={{ marginBottom: 0 }}>
            Required
          </label>
        </div>
        <button className="icon" title="Move up" onClick={() => onMove(-1)}>↑</button>
        <button className="icon" title="Move down" onClick={() => onMove(1)}>↓</button>
        <button className="icon" title="Duplicate" onClick={onDuplicate}>Duplicate</button>
        <button className="icon danger" onClick={onRemove}>Remove</button>
      </div>
    </div>
  );
}

/** `Trading since` -> `trading_since`. */
const slug = (label) =>
  String(label).toLowerCase().replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "").slice(0, 61).replace(/^[^a-z]*/, "");

/**
 * Keep the id in step with the question while the author has not chosen one.
 *
 * An id the author edited by hand is left alone — it is the key answers arrive
 * under, and silently rewriting it under them would move the data.
 */
function withDerivedId(field, label) {
  const auto = !field.id || field.id === slug(field.label || "");
  return auto ? { label, id: slug(label) } : { label };
}

function moved(list, from, to) {
  if (to < 0 || to >= list.length) return list;
  const next = [...list];
  next.splice(to, 0, next.splice(from, 1)[0]);
  return next;
}

/** Mirrors forms.validate_schema. Advisory only — the server decides. */
function schemaProblems(schema) {
  const sections = schema?.sections || [];
  if (sections.length === 0) return ["schema must contain at least one section"];

  const problems = [];
  const seen = new Set();

  sections.forEach((section, index) => {
    if (!String(section.title || "").trim()) {
      problems.push(`section ${index + 1} needs a title`);
      return;
    }
    const fields = section.fields || [];
    if (fields.length === 0) {
      problems.push(`section '${section.title}' has no fields`);
      return;
    }
    fields.forEach((field) => {
      const id = field.id || "";
      if (!FIELD_ID_RE.test(id)) {
        problems.push(
          `'${id}' is not a valid field id (lowercase letters, digits and underscores)`);
      } else if (seen.has(id)) {
        problems.push(`duplicate field id '${id}'`);
      } else {
        seen.add(id);
      }
      if (!String(field.label || "").trim()) problems.push(`field '${id}' needs a label`);
      if (field.type === "select" && !(field.options || []).filter(Boolean).length) {
        problems.push(`select field '${id}' needs at least one option`);
      }
    });
  });

  return problems;
}
