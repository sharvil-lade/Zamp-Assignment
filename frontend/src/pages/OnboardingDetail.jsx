import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import api from "../api";
import useResource from "../hooks/useResource";
import { Badge, ErrorBox, KeyValues, Loading } from "../components/ui";
import { copy } from "./NewOnboarding";

/**
 * One onboarding case: who it is for, which form it was created against, and
 * what can still be done with its invite link.
 */
export default function OnboardingDetail() {
  const { caseId } = useParams();
  const { data, error, loading, reload } = useResource(
    () => api.caseDetail(caseId), [caseId], `case:${caseId}`);

  if (loading && !data) return <Loading what="Loading the case" />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <div style={{ maxWidth: 680 }}>
      <div className="between" style={{ marginBottom: 16 }}>
        <div>
          <h1>{data.vendor_name}</h1>
          <p className="faint">Onboarding case <span className="mono">{data.case_id}</span></p>
        </div>
        <div className="row">
          <Badge status={data.status} label={data.status_label} />
          {data.run_id && (
            <Link className="btn primary" to={`/run/${data.run_id}`}>View run</Link>
          )}
        </div>
      </div>

      <section className="card">
        <h2>Case</h2>
        <KeyValues
          items={[
            ["Contact", data.contact_name],
            ["Email", data.contact_email],
            ["Created", data.created_at],
            ["Created by", data.created_by],
            ["Submitted", data.submitted_at],
            ["Run", data.run_id
              ? <Link className="mono" to={`/run/${data.run_id}`}>{data.run_id}</Link>
              : null],
          ]}
        />
      </section>

      <section className="card">
        <h2>Form</h2>
        {/* The case carries its own snapshot of the schema, so what the vendor
            saw stays reproducible even after the form is edited. */}
        <KeyValues
          items={[
            ["Form", data.form.template_id
              ? <Link to={`/forms/${data.form.template_id}`}>{data.form.template_name}</Link>
              : data.form.template_name],
          ]}
        />
      </section>

      <VendorLink data={data} />
    </div>
  );
}

/**
 * The case's one vendor link, shown whenever it is wanted.
 *
 * It is stable for the life of the case: the same URL takes the vendor to their
 * first submission and to every correction. What changes is only whether the
 * form behind it currently accepts one.
 */
function VendorLink({ data }) {
  const [copied, setCopied] = useState(false);
  const { url, open } = data.link;

  return (
    <section className="card">
      <div className="between">
        <h2 style={{ margin: 0 }}>Vendor submission form</h2>
        <Badge status={open ? "pending" : "neutral"}
               label={open ? "Open for submission" : "Closed"} />
      </div>

      <p className="mono" style={{ wordBreak: "break-all", fontSize: 13 }}>{url}</p>

      <div className="row" style={{ marginTop: 12 }}>
        <button className="primary" onClick={async () => {
          await copy(url);
          setCopied(true);
        }}>
          {copied ? "Copied" : "Copy link"}
        </button>
        <a className="btn" href={url} target="_blank" rel="noreferrer">Open form</a>
      </div>

      <p className="faint" style={{ marginBottom: 0, marginTop: 12 }}>
        {open
          ? "The vendor can submit once. The form closes as soon as they do."
          : "Already submitted. If the decision is Pending you can reopen it "
            + "from the run page — the link stays the same."}
      </p>
    </section>
  );
}
