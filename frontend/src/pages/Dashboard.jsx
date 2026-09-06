import { Link, useNavigate, useSearchParams } from "react-router-dom";
import api from "../api";
import useResource from "../hooks/useResource";
import { Badge, Empty, ErrorBox, Loading } from "../components/ui";

/**
 * Same metrics, same columns, same meaning as the server-rendered dashboard —
 * this is a rendering change, not a product change.
 */
export default function Dashboard() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status");
  const navigate = useNavigate();

  const { data, error, loading, reload } = useResource(
    () => api.dashboard(status), [status], `dashboard:${status || "all"}`);

  if (loading && !data) return <Loading what="Loading the dashboard" />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;

  const { stats, cases, active } = data;
  const awaitingVendorCount = cases.filter((item) => item.status === "AWAITING_VENDOR").length;
  const visibleCases = active
    ? cases.filter((item) => item.status === active)
    : cases;

  return (
    <>
      <div className="between" style={{ marginBottom: 16 }}>
        <div>
          <h1>Dashboard</h1>
          <p className="faint">History, status and outputs across every run.</p>
        </div>
        <Link className="btn primary" to="/onboardings/new">New onboarding</Link>
      </div>

      <div className="stats">
        {[
          ["Total", stats.total, null],
          ["Awaiting vendor", awaitingVendorCount, "AWAITING_VENDOR"],
          ["Approved", stats.approved, "APPROVED"],
          ["Pending", stats.pending, "PENDING"],
          ["Rejected", stats.rejected, "REJECTED"],
          ["AI Error", stats.error, "ERROR"],
        ].map(([label, value, filter]) => (
          <button
            key={label}
            className={`stat${active === filter && filter ? " on" : ""}`}
            onClick={() => setParams(filter ? { status: filter } : {})}
          >
            <div className="n">{value}</div>
            <div className="k">{label}</div>
          </button>
        ))}
      </div>

      <section className="card">
        <div>
          <h2 style={{ margin: 0 }}>Onboarding Cases</h2>
          {/* <p className="faint" style={{ margin: "2px 0 0" }}>
            One per vendor. Shows where the journey stands now.
          </p> */}
        </div>
        {visibleCases.length === 0 ? (
          <Empty>
            {active ? `No ${active.toLowerCase()} cases found.` : <>No onboarding cases yet. <Link to="/onboardings/new">Create one</Link> to
            send a vendor a secure link.</>}
          </Empty>
        ) : (
          <table className="cases">
            <thead>
              <tr>
                <th>Case</th>
                <th>Vendor</th>
                <th>Status</th>
                <th style={{ textAlign: "right" }}>Findings</th>
                <th>Created</th>
                <th>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {visibleCases.map((c) => (
                <tr
                  key={c.case_id}
                  className="clickable"
                  onClick={() =>
                    navigate(c.run_id ? `/run/${c.run_id}` : `/onboardings/${c.case_id}`)}
                >
                  <td className="mono faint">{c.case_id}</td>
                  <td style={{ fontWeight: 500 }}>{c.vendor_name}</td>
                  <td><Badge status={c.status} label={c.status_label} /></td>
                  <td style={{ textAlign: "right" }}>
                    {c.finding_count === null ? "—" : c.finding_count}
                  </td>
                  <td className="muted">{c.created_at}</td>
                  <td className="muted">
                    {c.last_activity_label}
                    <div className="faint">{c.last_activity_at}</div>
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
