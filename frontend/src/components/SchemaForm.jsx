/**
 * Renders a form schema. One component, driven entirely by data, so adding a
 * field to a form needs no React change at all.
 *
 * The schema shape is the one `backend/forms.py` validates:
 *   { sections: [ { title, description?, fields: [ { id, label, type,
 *                   required?, options?, help?, canonical? } ] } ] }
 *
 * Values are uncontrolled on purpose: the form is submitted as FormData, which
 * is what carries file uploads. React does not need to mirror every keystroke.
 */

const MONO = ["gstin", "pan", "ifsc", "account_number"];

const INPUT_TYPE = {
  text: "text",
  email: "email",
  phone: "tel",
  number: "number",
  date: "date",
};

export function Field({ field, defaultValue = "", accept, onFile = false }) {
  const { id, label, type, options, help, canonical } = field;
  // A document already held for this case satisfies the requirement. Only a
  // correction round can be in that state; a first submission never is.
  const required = field.required && !onFile;
  const control = renderControl();

  return (
    <div className="field">
      <label htmlFor={id}>
        {label}
        {required && <span className="req" title="Required">*</span>}
      </label>
      {control}
      {onFile && <p className="hint ok">Already received — attach a new file only to replace it.</p>}
      {help && <p className="hint">{help}</p>}
    </div>
  );

  function renderControl() {
    if (type === "document") {
      return <input id={id} name={id} type="file" accept={accept} required={required} />;
    }
    if (type === "textarea") {
      return <textarea id={id} name={id} defaultValue={defaultValue} required={required} />;
    }
    if (type === "select") {
      return (
        <select id={id} name={id} defaultValue={defaultValue} required={required}>
          <option value="">Select…</option>
          {(options || []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    }
    if (type === "boolean") {
      return (
        <div className="checkbox">
          <input id={id} name={id} type="checkbox" value="yes"
                 defaultChecked={defaultValue === "yes" || defaultValue === true} />
          <span className="faint">Yes</span>
        </div>
      );
    }
    return (
      <input
        id={id}
        name={id}
        type={INPUT_TYPE[type] || "text"}
        defaultValue={defaultValue}
        required={required}
        className={MONO.includes(canonical || id) ? "mono" : undefined}
      />
    );
  }
}

export default function SchemaForm({ schema, prefill = {}, accept, onFile = [] }) {
  const held = new Set(onFile);
  return (
    <>
      {(schema?.sections || []).map((section, index) => (
        <div className="card" key={section.title + index}>
          <h2>{section.title}</h2>
          {section.description && <p className="faint">{section.description}</p>}
          <div className="grid2">
            {(section.fields || []).map((field) => (
              <Field
                key={field.id}
                field={field}
                accept={accept}
                onFile={field.type === "document" && held.has(field.canonical || field.id)}
                defaultValue={prefill[field.canonical] ?? prefill[field.id] ?? ""}
              />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}
