import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import api from "../api";
import useResource from "../hooks/useResource";
import { Badge, Empty, ErrorBox, Loading } from "../components/ui";
import { copy } from "./NewOnboarding";

const POLL_MS = 1200;

/** Events whose payload is worth unfolding in the audit trail. */
const DETAILED = ["ai_call", "decision", "checks_evaluated", "stage_failed",
                  "followup_sent", "internal_note", "correction_requested"];

/**
 * One run, as a decision a person has to act on.
 *
 *      DECISION -> WHY -> FINDINGS -> EVIDENCE -> NEXT ACTION
 *
 * Everything else — the passed checks, the full extraction table, the AI
 * briefing, the timeline, the audit trail — is real and kept, but folded away.
 * A reviewer who reads only the top of this page should still know what
 * happened, why, and what to do about it.
 *
 * Nothing here is derived in the browser: the status, the counts, the plain
 * English and the correction list all come from the engine.
 */
export default function RunDetail() {
  const { runId } = useParams();
  const { data, error, loading, reload, setData } = useResource(
    () => api.run(runId), [runId], `run:${runId}`);

  // `finished` is the authoritative terminal marker. The status is durable
  // before the last stage runs, so polling on status would stop the page mid-run.
  const running = Boolean(data) && !data.finished;
  useEffect(() => {
    if (!running) return;
    const control = new AbortController();
    const timer = setInterval(
      () => api.run(runId, control.signal).then(setData).catch(() => {}), POLL_MS);
    return () => {
      clearInterval(timer);
      control.abort();
    };
  }, [running, runId, setData]);

  if (loading && !data) return <Loading what="Loading the run" />;
  if (error?.status === 404) {
    return (
      <Empty>
        No run with id <span className="mono">{runId}</span>.{" "}
        <Link to="/dashboard">Back to the dashboard</Link>
      </Empty>
    );
  }
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;

  const run = data;
  const blocking = run.findings.filter((f) => f.severity === "BLOCK");
  const corrections = run.findings.filter((f) => f.severity === "FIX");

  return (
    <>
      <Header run={run} />
      {run.finished
        ? <Decision run={run} blocking={blocking} corrections={corrections} />
        : <Processing stages={run.stages} />}

      {blocking.length > 0 && (
        <FindingGroup
          title="Blocking issues"
          count={blocking.length}
          note="The evidence contradicts the submission. This cannot be approved as it stands."
          findings={blocking} />
      )}

      {corrections.length > 0 && (
        <FindingGroup
          title="Needs correction"
          count={corrections.length}
          note="Missing, unreadable or unclear — the vendor can fix these."
          findings={corrections} />
      )}

      {run.status === "PENDING" && <Corrections run={run} onChange={reload} />}

      {run.internal_note && <InternalNote note={run.internal_note} />}
      {run.rejected_uploads.length > 0 && (
        <RejectedUploads uploads={run.rejected_uploads} />
      )}

      <Secondary run={run} />
    </>
  );
}

/* ==========================================================================
   HEADER
   ========================================================================== */

function Header({ run }) {
  const c = run.case_status;
  // The case status is the case's, not this run's: reading an older submission
  // must not suggest the vendor is still waiting when they were approved on a
  // later one.
  const historical = c && c.latest_run_id && c.latest_run_id !== run.run_id;

  return (
    <>
      <div className="between" style={{ marginBottom: 16 }}>
        <div>
          <h1 style={{ marginBottom: 4 }}>{run.vendor_name || "Unnamed vendor"}</h1>
          <p className="faint" style={{ margin: 0 }}>
            <span className="mono">{run.run_id}</span>
            {run.case_id && (
              <>
                {" · "}
                <Link className="mono" to={`/onboardings/${run.case_id}`}>{run.case_id}</Link>
              </>
            )}
            {" · Submitted "}{run.created_at}
            {c && (
              <>
                {" · Case: "}
                <Badge status={c.status} label={c.status_label} />
              </>
            )}
          </p>
        </div>
      </div>
      {run.rounds.length > 1 && <Submissions rounds={run.rounds} />}
      {historical && (
        <p className="faint" style={{ marginTop: -4, marginBottom: 16 }}>
          You are reading an earlier submission. The case moved on.
        </p>
      )}
    </>
  );
}

/**
 * One tab per submission on this case. A correction produces a new run rather
 * than overwriting the old one, so the history is real and worth reading: three
 * issues, then one, then none.
 */
function Submissions({ rounds }) {
  return (
    <nav className="subs" aria-label="Submissions">
      {rounds.map((r) => (
        <Link key={r.run_id} to={`/run/${r.run_id}`}
              className={`sub ${r.current ? "on" : ""} ${r.status.toLowerCase()}`}
              aria-current={r.current ? "page" : undefined}>
          <span className="sub-n">
            Submission {r.round}{r.latest && <span className="sub-tag">Latest</span>}
          </span>
          <span className="sub-s">
            {r.status_label} · {r.finding_count}{" "}
            {r.finding_count === 1 ? "issue" : "issues"}
          </span>
        </Link>
      ))}
    </nav>
  );
}

/* ==========================================================================
   WHILE IT RUNS
   ========================================================================== */

/**
 * The eight pipeline stages, grouped into the five a reviewer thinks in.
 * `completeness` and `format` are both "validation" — one checks the form, the
 * other the documents — and the last three are all part of reaching a decision.
 */
const PIPELINE = [
  { label: "Intake", stages: ["intake"],
    saying: "Receiving the submission…" },
  { label: "Extraction", stages: ["extraction"],
    saying: "Reading the uploaded documents…" },
  { label: "Validation", stages: ["completeness", "format"],
    saying: "Validating required fields and documents…" },
  { label: "Consistency", stages: ["consistency"],
    saying: "Cross-checking the submission against the documents…" },
  { label: "Decision", stages: ["decision", "review", "communicate"],
    saying: "Producing the decision…" },
];

/** A step is only as far along as its least-advanced stage. */
function stepState(step, byKey) {
  const states = step.stages.map((k) => byKey[k]?.state ?? "pending");
  if (states.includes("failed")) return "failed";
  if (states.includes("running")) return "running";
  // A skipped stage is settled, not outstanding — a run with no documents
  // still finishes extraction.
  if (states.every((st) => st === "done" || st === "skipped")) return "done";
  return "pending";
}

const MARK = { done: "✓", running: "◉", failed: "!", pending: "○" };

/**
 * What is happening, right now, from the events the pipeline has actually
 * written. Nothing here animates on a timer or guesses at progress: if the
 * backend has not reached a stage, the step stays muted.
 */
function Processing({ stages }) {
  const byKey = Object.fromEntries(stages.map((s) => [s.key, s]));
  const steps = PIPELINE.map((step) => ({ ...step, state: stepState(step, byKey) }));
  const active = steps.find((s) => s.state === "running")
    || steps.find((s) => s.state === "failed");

  return (
    <section className="card running-card">
      <ol className="steps" aria-label="Pipeline progress">
        {steps.map((s) => (
          <li key={s.label} className={s.state}
              aria-current={s.state === "running" ? "step" : undefined}>
            <span className="steps-mark">
              {s.state === "running" ? <span className="spin" /> : MARK[s.state]}
            </span>
            {s.label}
          </li>
        ))}
      </ol>
      <p className="steps-saying">
        {active?.state === "failed"
          ? `${active.label} failed — see the audit trail.`
          : active?.saying || "Starting…"}
      </p>
    </section>
  );
}

/* ==========================================================================
   THE DECISION — the hero
   ========================================================================== */

function Decision({ run, blocking, corrections }) {
  const d = run.decision;
  const counts = run.checks?.summary;
  const clean = run.finished && !blocking.length && !corrections.length;

  return (
    <section className={`card decision ${run.status.toLowerCase()}`}>
      <div className="row" style={{ gap: 16, alignItems: "baseline" }}>
        <span className="verdict">
          {run.finished ? run.status_label : "Processing"}
        </span>
        <span className="verdict-line">
          {!run.finished && <span className="spin" />} {d.headline}
        </span>
      </div>

      {d.summary && <p className="lead">{d.summary}</p>}

      {counts && (
        <p className="counts">
          <b>{counts.total}</b> checks
          {" · "}<b>{counts.passed}</b> passed
          {counts.skipped > 0 && (
            <span title="Not applicable to this submission — a PAN check is never run against a US vendor">
              {" · "}<b>{counts.skipped}</b> not applicable
            </span>
          )}
          {counts.corrections > 0 && (
            <span className="warn">{" · "}<b>{counts.corrections}</b> need correction</span>
          )}
          {counts.blocking > 0 && (
            <span className="bad">{" · "}<b>{counts.blocking}</b> blocking</span>
          )}
          {run.duration_ms !== null && (
            <span className="faint">{" · decided in "}{run.duration_ms} ms</span>
          )}
        </p>
      )}

      {clean && run.status === "APPROVED" && (
        <p className="muted" style={{ marginBottom: 0 }}>
          No inconsistency was found between what the vendor submitted and what
          their documents say.
        </p>
      )}

      {d.next_action && (
        <div className="next">
          <strong>Next action</strong>
          <p style={{ margin: "4px 0 0" }}>{d.next_action}</p>
        </div>
      )}
    </section>
  );
}

/* ==========================================================================
   FINDINGS — why, with the evidence attached to each one
   ========================================================================== */

function FindingGroup({ title, count, note, findings }) {
  return (
    <section className="card">
      <div className="between">
        <h2 style={{ margin: 0 }}>{title}</h2>
        <span className="faint">{count}</span>
      </div>
      <p className="faint" style={{ marginTop: 4 }}>{note}</p>
      {findings.map((f, i) => <FindingCard key={`${f.rule_id}-${i}`} f={f} />)}
    </section>
  );
}

/**
 * One finding, told so a reviewer never has to read rule-engine output: the
 * plain sentence first, then the two values that prove it, labelled by where
 * each came from. The rule's purpose is a tooltip rather than a paragraph —
 * repeating it seventeen times is what made the old page unreadable.
 */
function FindingCard({ f }) {
  const blocks = f.severity === "BLOCK";
  return (
    <div className={`finding ${blocks ? "block" : "fix"}`}>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <span className="rid mono" title={f.purpose}>{f.rule_id}</span>
        <strong>{f.name}</strong>
        <Badge status={blocks ? "rejected" : "pending"}
               label={blocks ? "BLOCK" : "FIX"} />
        <span className="badge neutral">{f.category}</span>
        {f.tag === "ai_uncertain" && (
          <span className="badge ai"
                title="The name comparison was not confident, so it was routed to a human instead of being decided">
            AI unsure — your judgement
          </span>
        )}
      </div>

      <p style={{ margin: "8px 0 0" }}>{f.message}</p>

      {f.evidence_rows.length > 0 && (
        <table className="evidence">
          <tbody>
            {f.evidence_rows.map((row) => (
              <tr key={row.source}>
                <td className="muted">{row.source}</td>
                <td className="mono">{row.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/* ==========================================================================
   NEXT ACTION — closing the loop with the vendor
   ========================================================================== */

/**
 * The correction cycle. The list is generated from this run's own findings, so
 * two Pending runs never get the same message.
 *
 * The link is *issued*, never recovered: only a hash of the token is stored, so
 * it can be handed out but never looked up again. Nothing leaves the system on
 * its own — a person passes the link to the vendor, and that is the whole loop.
 */
function Corrections({ run }) {
  const { correction } = run;
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(null);
  const url = correction.vendor_url;

  // Re-enables the form behind the case's existing URL, and nothing else.
  // Deliberately does not navigate: the reviewer stays on the run they are
  // reading, and the link is theirs to send when they choose.
  async function openForm() {
    setBusy(true);
    setError(null);
    try {
      await api.reopenCase(correction.case_id);
      // Re-read rather than tracking it locally, so the badge and the wording
      // below can never disagree with the server about the gate.
      onChange();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="between">
        <h2 style={{ margin: 0 }}>Vendor correction</h2>
        <Badge status="pending" label={correction.status_label} />
      </div>

      {correction.items.length > 0 && (
        <ol className="corrections">
          {correction.items.map((item, i) => (
            <li key={i}>
              {item.text}
              <span className="faint"> · {item.category}</span>
            </li>
          ))}
        </ol>
      )}

      <ErrorBox error={error} />

      <div className="linkbar">
        <span className="faint">
          The vendor's own form, at the case's one permanent link:
        </span>
        <input className="mono" readOnly value={url}
               onFocus={(e) => e.target.select()} />
        <div className="row">
          {correction.can_reopen && (
            <button className="primary" onClick={openForm} disabled={busy}>
              {busy ? "Opening…" : "Open correction form"}
            </button>
          )}
          <button onClick={async () => {
            await copy(url);
            setCopied(true);
          }}>
            {copied ? "Copied" : "Copy link"}
          </button>
        </div>
        <span className="faint">
          {correction.awaiting_correction
            ? "The form is open. Their corrections become a new submission on "
              + "this same case, prefilled with what they sent."
            : "Opening re-enables this same link — it does not create a new case "
              + "or a new URL."}
        </span>
      </div>
    </section>
  );
}

function InternalNote({ note }) {
  return (
    <section className="card">
      <h2>Internal note</h2>
      <p>{note.note}</p>
      <p className="faint" style={{ marginBottom: 0 }}>
        Blocking rules <span className="mono">{(note.blocking_rules || []).join(", ")}</span>.
        No vendor-facing message was drafted — telling a rejected party which
        check caught them is deliberate policy, not an omission.
      </p>
    </section>
  );
}

function RejectedUploads({ uploads }) {
  return (
    <div className="alert warn">
      <strong>Attachments we could not read</strong>
      <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        {uploads.map((r, i) => (
          <li key={i}>
            <span className="mono">{r.filename}</span> — {r.reason}. It was not
            saved, so this document counts as not attached.
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ==========================================================================
   SECONDARY — kept, available, and out of the way
   ========================================================================== */

function Secondary({ run }) {
  const custom = Object.entries(run.custom_answers || {});
  return (
    <section className="card secondary">
      <h2>Detail</h2>
      <p className="faint" style={{ marginTop: 0 }}>
        Everything behind the decision, in full. Nothing here changes it.
      </p>

      <Timeline stages={run.stages} />

      {run.checks && (
        <Fold summary={`All checks · ${run.checks.summary.passed} passed of ${run.checks.summary.total}`}>
          <CheckRegister checks={run.checks} />
        </Fold>
      )}

      {run.has_documents && (
        <Fold summary="Extracted vs submitted · every field we read off each document">
          <Comparisons comparisons={run.comparisons} />
        </Fold>
      )}

      {run.ai_summary && (
        <Fold summary="Onboarding Assistant briefing — advisory">
          <AiSummary summary={run.ai_summary} />
        </Fold>
      )}

      {custom.length > 0 && (
        <Fold summary={`Form-specific answers · ${custom.length}`}>
          <p className="faint">
            From a custom form. No PS-2 rule examined these — they are recorded
            for a human.
          </p>
          <table>
            <tbody>
              {custom.map(([id, value]) => (
                <tr key={id}>
                  <td className="muted">{id}</td>
                  <td className="mono">
                    {Array.isArray(value) ? value.join(", ") : String(value ?? "") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Fold>
      )}

      <Fold summary={`Audit trail · ${run.events.length} events`}>
        <p className="faint">
          Append-only. Every model call is recorded with its exact response and
          token usage; nothing in the app can delete a row of this.
        </p>
        <AuditTrail events={run.events} />
      </Fold>
    </section>
  );
}

/** Native <details>, so folding costs no state and no library. */
function Fold({ summary, children }) {
  return (
    <details className="fold">
      <summary>{summary}</summary>
      <div className="fold-body">{children}</div>
    </details>
  );
}

/** One line: what ran, in order, and where it is now. */
function Timeline({ stages }) {
  return (
    <ol className="timeline" aria-label="Processing timeline">
      {stages.map((s) => (
        <li key={s.key} className={s.state} title={s.result || s.label}>
          <span className={`dot ${s.state}`} />
          {s.label}
          {s.ai && <span className="badge ai">AI</span>}
        </li>
      ))}
    </ol>
  );
}

function CheckRegister({ checks }) {
  return (
    <>
      {checks.categories.map((cat) => (
        <div key={cat.name} className="cat-block">
          <div className="between">
            <strong>{cat.name}</strong>
            <span className="faint">
              {cat.failed > 0 ? `${cat.failed} failed · ` : ""}
              {cat.passed} passed{cat.skipped > 0 && ` · ${cat.skipped} n/a`}
            </span>
          </div>
          <table className="checks">
            <tbody>
              {cat.checks.map((c) => (
                <tr key={c.rule_id} className={c.state}>
                  <td className="mono faint">{c.rule_id}</td>
                  <td title={c.purpose}>{c.name}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <StateBadge state={c.state} impact={c.impact} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}

function StateBadge({ state, impact }) {
  if (state === "passed") return <span className="badge approved">Passed</span>;
  if (state === "skipped") {
    return (
      <span className="badge neutral"
            title="This rule had nothing to judge on this submission, so it did not run — it is not counted as a pass">
        Not applicable
      </span>
    );
  }
  return (
    <span className={`badge ${impact.includes("BLOCK") ? "rejected" : "pending"}`}>
      Failed
    </span>
  );
}

function Comparisons({ comparisons }) {
  return (
    <>
      <p className="faint">
        What the Onboarding Assistant read off each document, beside what the vendor
        typed. A row is marked as a mismatch only where a rule compares the pair.
      </p>
      {comparisons.map((c) => (
        <div key={c.key} className="cat-block">
          <div className="row" style={{ gap: 6 }}>
            <strong>{c.label}</strong>
            {!c.attached && <span className="badge neutral">not attached</span>}
          </div>
          {c.attached && (
            <table className="compare">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Submitted</th>
                  <th>On document</th>
                  <th>Rule</th>
                </tr>
              </thead>
              <tbody>
                {c.rows.map((r) => (
                  <tr key={r.label}>
                    <td className="muted">{r.label}</td>
                    <td className="mono">{r.form || "—"}</td>
                    <td className="mono">
                      {r.doc || "—"}
                      {r.mismatch && <span className="badge rejected">mismatch</span>}
                    </td>
                    <td className={`mono ${r.mismatch ? "" : "faint"}`}>
                      {r.rule || ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </>
  );
}

/** Advisory. It runs after the decision and cannot change it. */
function AiSummary({ summary }) {
  return (
    <>
      <p className="faint">
        Written after the decision, by the Onboarding Assistant. The rule engine decided;
        this explains and flags what a reviewer might want to double-check.
        Risk level <strong>{summary.risk}</strong> is derived from finding
        severity, not from the model.
      </p>
      {summary.summary && <p>{summary.summary}</p>}
      {summary.recommended_action && (
        <p><strong>Recommended:</strong> {summary.recommended_action}</p>
      )}
      {summary.risk_rationale && <p className="faint">{summary.risk_rationale}</p>}
      {summary.extraction_notes?.length > 0 && (
        <div className="alert warn" style={{ marginBottom: 0 }}>
          <strong>Worth double-checking</strong>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {summary.extraction_notes.map((note, i) => <li key={i}>{note}</li>)}
          </ul>
        </div>
      )}
    </>
  );
}

function AuditTrail({ events }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Time</th><th>Stage</th><th>Event</th><th>Actor</th>
          <th style={{ textAlign: "right" }}>Duration</th>
        </tr>
      </thead>
      <tbody>
        {events.map((e) => (
          <tr key={e.id}>
            <td className="faint" title={e.ts}>{e.ts_display}</td>
            <td className="muted">{e.stage}</td>
            <td>
              {e.event_type}
              {e.detail && DETAILED.includes(e.event_type) && (
                <details style={{ marginTop: 4 }}>
                  <summary className="faint">{eventSummary(e)}</summary>
                  <pre className="mono" style={{ marginTop: 4, whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(e.detail, null, 2)}
                  </pre>
                </details>
              )}
            </td>
            <td className="faint">{e.actor}</td>
            <td className="faint" style={{ textAlign: "right" }}>
              {e.duration_ms === null ? "" : `${e.duration_ms} ms`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function eventSummary(e) {
  const usage = e.detail.usage;
  if (e.event_type === "ai_call" && usage) {
    return `${e.detail.model} · ${usage.input_tokens} in / ${usage.output_tokens} out`;
  }
  if (e.event_type === "checks_evaluated") {
    const s = e.detail.summary;
    return `${s.total} checks · ${s.passed} passed · ${s.failed} failed`;
  }
  return "detail";
}
