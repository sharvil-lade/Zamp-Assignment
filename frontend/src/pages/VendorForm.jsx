import { useState } from "react";
import { useParams } from "react-router-dom";
import api from "../api";
import useResource from "../hooks/useResource";
import SchemaForm from "../components/SchemaForm";
import { ErrorBox, Loading } from "../components/ui";

/**
 * The vendor portal. Public, and rendered outside the employee layout.
 *
 * The token in the URL is the only authorisation, so this page shows a vendor
 * exactly two things: the form they were invited to fill in, and confirmation
 * that it arrived. Never a decision, a finding, a risk level, an AI note, a run
 * or case id, an employee name, or a link back into the employee app.
 */
export default function VendorForm() {
  const { token } = useParams();
  const { data, error, loading } = useResource(() => api.vendorForm(token), [token]);
  const [done, setDone] = useState(false);
  const [failed, setFailed] = useState(null);
  const [missing, setMissing] = useState([]);

  /**
   * Nothing is sent until every required field and every required document is
   * filled in. The browser already knows which those are — the schema marks
   * them — so this only turns its one-at-a-time bubble into a list the vendor
   * can work through, and marks the offending controls.
   *
   * The engine's completeness rules still run on whatever arrives. This is the
   * courtesy layer; that one is the guarantee.
   */
  function incomplete(form) {
    form.classList.add("checked");
    // A file input holding a file is not missing, whatever ValidityState says:
    // jsdom reports valueMissing for a required file input that has one, so
    // relying on checkValidity alone would make this untestable.
    const invalid = [...form.elements].filter(
      (el) => el.willValidate && !el.checkValidity()
              && !(el.type === "file" && el.files?.length));
    setMissing(invalid.map((el) => ({
      name: el.name,
      label: el.labels?.[0]?.textContent?.replace("*", "").trim() || el.name,
    })));
    if (invalid.length) {
      invalid[0].scrollIntoView?.({ block: "center", behavior: "smooth" });
      form.reportValidity();
    }
    return invalid.length > 0;
  }

  function onSubmit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (incomplete(form)) return;
    setFailed(null);

    // Deliberately not awaited. The decision pipeline runs inside this request
    // and takes the better part of a minute, but the vendor's part finished the
    // moment their upload left — making them watch a spinner for the engine's
    // work is our problem leaking into their page.
    //
    // The request stays in flight while they read the confirmation, which is
    // what keeps the work inside a request the host is holding open. Deferring
    // it to a background task instead would not survive a serverless response.
    api.vendorSubmit(token, new FormData(form)).catch((err) => {
      // 409 means the link was already used. Same outcome for the vendor as
      // submitting now — their details are in — so leave the confirmation up.
      if (err.status !== 409) setFailed(err);
    });
    setDone(true);
  }

  return (
    <>
      <header className="header">
        <div className="header-inner">
          <span className="brand">Vendor registration<span>Secure link</span></span>
        </div>
      </header>
      <main className="narrow">{body()}</main>
    </>
  );

  function body() {
    if (loading) return <Loading what="Loading your form" />;

    // A bad token says nothing about why: not whether the case exists, not
    // whether it expired, not whether it was already used.
    if (error?.status === 404) {
      return (
        <>
          <h1>This onboarding link is not valid</h1>
          <p className="faint">
            Please use the most recent link you were sent, or reply to the person
            who invited you and ask for a new one.
          </p>
        </>
      );
    }
    if (error) return <ErrorBox error={error} />;
    if (!data) return null;
    // A failure after the confirmation is up must replace it, not hide behind it.
    if (failed) return <ErrorBox error={failed} />;
    if (done || data.state === "submitted") return <Received vendorName={data.vendor_name} />;

    return (
      <>
        <h1>{data.vendor_name}</h1>
        <p className="faint" style={{ marginBottom: 16 }}>
          Please confirm your company details and attach the documents below.
          Everything is submitted once, securely, through this link.
        </p>

        <ErrorBox error={failed} />

        <form onSubmit={onSubmit} noValidate>
          <SchemaForm
            schema={data.schema}
            prefill={data.prefill}
            accept={data.accepted_types.join(",")}
            onFile={data.on_file}
          />
          <p className="hint">
            {data.accepted_types.join(", ")} — up to {data.max_upload_mb} MB per file.
          </p>

          {missing.length > 0 && (
            <div className="alert bad" role="alert">
              <strong>
                {missing.length} item{missing.length === 1 ? "" : "s"} still needed
                before you can submit:
              </strong>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {missing.map((f) => <li key={f.name}>{f.label}</li>)}
              </ul>
            </div>
          )}

          <div className="row" style={{ marginTop: 16 }}>
            <button className="primary" type="submit">Submit</button>
            <span className="faint">
              Every field and every document is required. You can only submit once.
            </span>
          </div>
        </form>

        <p className="faint" style={{ marginTop: 24 }}>
          This link is unique to your company. Please do not forward it.
        </p>
      </>
    );
  }
}

/** Confirmation only: no status, no timeline, nothing to come back and check. */
function Received({ vendorName }) {
  return (
    <>
      <div className="alert ok" role="status">
        Thank you — your details are with the onboarding team.
      </div>
      <div className="card">
        <p>We have the registration for <strong>{vendorName}</strong>.</p>
        <p className="faint" style={{ marginBottom: 0 }}>
          If anything is missing or needs correcting, we will contact you.
          You can close this page.
        </p>
      </div>
    </>
  );
}
