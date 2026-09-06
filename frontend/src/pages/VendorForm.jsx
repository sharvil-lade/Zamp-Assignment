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
  const [busy, setBusy] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setFailed(null);
    try {
      await api.vendorSubmit(token, new FormData(event.currentTarget));
      setDone(true);
    } catch (err) {
      // 409 means the link was already used. That is the same outcome for the
      // vendor as submitting now — their details are in — so say so plainly.
      if (err.status === 409) setDone(true);
      else setFailed(err);
    } finally {
      setBusy(false);
    }
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
    if (done || data.state === "submitted") return <Received vendorName={data.vendor_name} />;

    return (
      <>
        <h1>{data.vendor_name}</h1>
        <p className="faint" style={{ marginBottom: 16 }}>
          Please confirm your company details and attach the documents below.
          Everything is submitted once, securely, through this link.
        </p>

        <ErrorBox error={failed} />

        <form onSubmit={onSubmit}>
          <SchemaForm
            schema={data.schema}
            prefill={data.prefill}
            accept={data.accepted_types.join(",")}
          />
          <p className="hint">
            {data.accepted_types.join(", ")} — up to {data.max_upload_mb} MB per file.
          </p>
          <div className="row" style={{ marginTop: 16 }}>
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Submitting…" : "Submit"}
            </button>
            <span className="faint">You can only submit once.</span>
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
