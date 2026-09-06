/**
 * The run view is where a reviewer decides. Two things must never regress: the
 * page must keep polling until the run is genuinely finished, and it must never
 * present the AI as the decider.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RunDetail from "../pages/RunDetail";

vi.mock("../api", () => {
  const api = { run: vi.fn(), reopenCase: vi.fn() };
  return { default: api, api, ApiError: class ApiError extends Error {} };
});

import api from "../api";

const STAGES = [
  { key: "intake", label: "Intake", ai: false, state: "done", ms: 3, detail: null,
    result: "Submission received" },
  { key: "completeness", label: "Completeness", ai: false, state: "done", ms: 1,
    detail: null, result: "2 issues raised" },
  { key: "consistency", label: "Consistency", ai: true, state: "done", ms: 402,
    detail: null, result: "1 issue raised" },
  { key: "decision", label: "Decision", ai: false, state: "done", ms: 1, detail: null,
    result: "Decision: PENDING" },
];

/** The check register, in the shape `checks_view` builds it. */
const CHECKS = {
  summary: { total: 17, evaluated: 15, passed: 12, failed: 3, skipped: 2,
             blocking: 1, corrections: 2 },
  categories: [
    {
      name: "Banking consistency", passed: 1, failed: 1, skipped: 0,
      checks: [
        { rule_id: "R09", name: "Bank account holder is the vendor",
          category: "Banking consistency", stage: "consistency",
          purpose: "Money going to a personal account is the expensive failure.",
          impact: ["BLOCK", "FIX"], evidence: ["Cancelled cheque or bank letter",
                                               "Vendor submission"],
          state: "failed", findings: [] },
        { rule_id: "R10", name: "Bank details match the bank document",
          category: "Banking consistency", stage: "consistency",
          purpose: "A mistyped account number pays a stranger.",
          impact: ["BLOCK"], evidence: null, state: "passed", findings: [] },
      ],
    },
    {
      name: "Identifier & format validation", passed: 1, failed: 0, skipped: 1,
      checks: [
        { rule_id: "R03", name: "PAN format",
          category: "Identifier & format validation", stage: "format",
          purpose: "The PAN is the spine of Indian tax identity.",
          impact: ["FIX"], evidence: null, state: "passed", findings: [] },
        { rule_id: "R05", name: "IFSC format",
          category: "Identifier & format validation", stage: "format",
          purpose: "The IFSC routes the payment.",
          impact: ["FIX"], evidence: null, state: "skipped", findings: [] },
      ],
    },
  ],
};

const RUN = {
  run_id: "VS-2001",
  vendor_name: "Meridian Textiles Pvt Ltd",
  status: "PENDING",
  status_label: "Pending",
  finished: true,
  created_at: "Sep 5, 2026 · 2:14 PM",
  duration_ms: 1840,
  case_id: "OC-1001",
  blocks: 0,
  fixes: 2,
  uncertain: 1,
  stages: STAGES,
  decision: {
    status: "PENDING",
    status_label: "Pending",
    headline: "2 items need correction",
    summary: "Certificate of Incorporation was not attached.",
    reason: "Nothing contradicts the submission - the evidence is incomplete.",
    next_action: "Request the outstanding items from the vendor.",
  },
  checks: CHECKS,
  findings: [
    { rule_id: "R02", severity: "FIX", stage: "completeness",
      name: "Required documents", category: "Completeness",
      purpose: "Typed details are a claim; the documents are the evidence.",
      message: "Certificate of Incorporation was not attached",
      expected: null, actual: null, tag: null, evidence_rows: [] },
    { rule_id: "R09", severity: "BLOCK", stage: "consistency",
      name: "Bank account holder is the vendor", category: "Banking consistency",
      purpose: "Money going to a personal account is the expensive failure.",
      message: "Account holder does not match the legal entity name",
      expected: "Meridian Exports", actual: "Meridian Textiles Pvt Ltd",
      tag: null,
      evidence_rows: [
        { source: "Cancelled cheque or bank letter", value: "Meridian Exports",
          evidence: true },
        { source: "Vendor submission", value: "Meridian Textiles Pvt Ltd",
          evidence: false },
      ] },
    { rule_id: "R12", severity: "FIX", stage: "consistency",
      name: "Incorporation certificate names the vendor",
      category: "Identity & tax consistency",
      purpose: "Confirms the entity onboarded is the entity incorporated.",
      message: "Name similarity was inconclusive", expected: null, actual: null,
      tag: "ai_uncertain", evidence_rows: [] },
  ],
  comparisons: [],
  has_documents: false,
  custom_answers: { trading_since: "2019-04-01" },
  rejected_uploads: [],
  ai_summary: {
    risk: "Medium",
    risk_rationale: "Two fixable gaps, nothing disqualifying.",
    summary: "The vendor is registered and traceable but the file is incomplete.",
    key_points: ["Incorporation certificate missing"],
    recommended_action: "Ask the vendor for the incorporation certificate.",
  },
  internal_note: null,
  correction: {
    case_id: "OC-1001",
    awaiting_correction: false,
    can_reopen: true,
    vendor_url: "http://localhost/vendor/onboard/tok123",
    status_label: "Corrections not requested yet",
    items: [
      { rule_id: "R02", category: "Completeness",
        text: "Please upload your certificate of incorporation." },
      { rule_id: "R12", category: "Identity & tax consistency",
        text: "Please review and correct: name similarity was inconclusive." },
    ],
  },
  rounds: [],
  case_status: { case_id: "OC-1001", status: "PENDING", status_label: "Pending",
                 latest_run_id: "VS-2001" },
  events: [
    { id: 1, ts: "2026-09-05T14:14:07+00:00", ts_display: "Sep 5, 2026 · 2:14 PM",
      stage: "intake", event_type: "vendor_submitted", actor: "vendor",
      label: "Vendor submitted", duration_ms: null, detail: { case_id: "OC-1001" } },
    { id: 2, ts: "2026-09-05T14:14:09+00:00", ts_display: "Sep 5, 2026 · 2:14 PM",
      stage: "decision", event_type: "run_finished", actor: "system",
      label: "Processing complete", duration_ms: null, detail: null },
  ],
};

function renderRun() {
  return render(
    <MemoryRouter initialEntries={["/run/VS-2001"]}>
      <Routes>
        <Route path="/run/:runId" element={<RunDetail />} />
        <Route path="/dashboard" element={<p>dashboard page</p>} />
        <Route path="/onboardings/:caseId" element={<p>case page</p>} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.useRealTimers();
  api.run.mockResolvedValue(RUN);
  api.reopenCase.mockResolvedValue({
    vendor_url: "http://localhost/vendor/onboard/tok123", open: true });
});

/** The page is loaded once the decision hero is on screen. */
const loaded = () => screen.findByText("2 items need correction");

describe("what the page shows", () => {
  it("shows the vendor, the run, the decision and the timing", async () => {
    renderRun();
    expect(await screen.findByRole("heading", { name: "Meridian Textiles Pvt Ltd" }))
      .toBeInTheDocument();
    expect(screen.getByText("VS-2001")).toBeInTheDocument();
    // "Pending" is both the status badge and the AI card's echo of the decision.
    expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);
    expect(screen.getByText(/1840 ms/)).toBeInTheDocument();
  });

  it("lists every pipeline stage and badges the ones that used AI", async () => {
    renderRun();
    const processing = await screen.findByLabelText("Processing timeline");
    for (const stage of ["Intake", "Completeness", "Consistency", "Decision"]) {
      expect(within(processing).getByText(stage)).toBeInTheDocument();
    }
    expect(within(processing).getAllByText("AI").length).toBeGreaterThan(0);
  });

  it("puts blocking findings before fixable ones", async () => {
    renderRun();
    await loaded();
    // Blocking is its own section and comes first, so the worst news is not
    // buried under three fixable ones.
    const headings = [...document.querySelectorAll("h2")].map((n) => n.textContent);
    expect(headings.indexOf("Blocking issues"))
      .toBeLessThan(headings.indexOf("Needs correction"));
    const ids = [...document.querySelectorAll(".finding .rid")].map((n) => n.textContent);
    expect(ids[0]).toBe("R09");
  });

  it("shows each finding's evidence beside the vendor's own answer, labelled by source",
    async () => {
      renderRun();
      await loaded();
      const row = (await screen.findByText("Meridian Exports")).closest("tr");
      // The label is what makes the value checkable: a bare pair of strings does
      // not say which one came off the cheque.
      expect(within(row).getByText("Cancelled cheque or bank letter"))
        .toBeInTheDocument();
    });

  it("leads with the decision, its reason and the next action", async () => {
    renderRun();
    expect(await screen.findByText("2 items need correction")).toBeInTheDocument();
    expect(screen.getByText(/Request the outstanding items/)).toBeInTheDocument();
  });

  it("counts the checks from the backend rather than hardcoding a total", async () => {
    renderRun();
    await loaded();
    const counts = document.querySelector(".counts");
    expect(counts.textContent).toContain("17");
    expect(counts.textContent).toContain("12");
  });

  it("names a skipped check as not applicable, never as a pass", async () => {
    renderRun();
    await loaded();
    expect(screen.getAllByText("Not applicable").length).toBeGreaterThan(0);
  });

  it("groups the check register into business categories", async () => {
    renderRun();
    await loaded();
    // The full register is folded away, but present and grouped by category.
    const register = document.querySelector(".fold .cat-block").closest(".fold");
    expect(within(register).getByText("Banking consistency")).toBeInTheDocument();
    expect(within(register).getByText("Identifier & format validation"))
      .toBeInTheDocument();
  });

  it("separates custom form answers from anything a rule decided", async () => {
    renderRun();
    await loaded();
    expect(screen.getByText(/Form-specific answers/)).toBeInTheDocument();
    expect(screen.getByText(/No PS-2 rule examined these/i)).toBeInTheDocument();
    expect(screen.getByText("2019-04-01")).toBeInTheDocument();
  });

  it("renders the audit trail", async () => {
    renderRun();
    await loaded();
    // Kept in full, but folded: it must not dominate the decision.
    expect(screen.getByText(/Audit trail · 2 events/)).toBeInTheDocument();
    expect(screen.getByText("vendor_submitted")).toBeInTheDocument();
  });
});

describe("the AI is advisory, never the decider", () => {
  it("labels the AI summary as advisory and names the rule engine as the decider",
    async () => {
      renderRun();
      await loaded();
      expect(screen.getByText(/Onboarding Assistant briefing — advisory/)).toBeInTheDocument();
      expect(screen.getByText(/The rule engine decided/)).toBeInTheDocument();
    });

  it("shows the deterministic decision inside the AI card rather than an AI verdict",
    async () => {
      renderRun();
      await loaded();
      // The verdict is the engine's, stated in the hero — not inside the AI card.
      expect(document.querySelector(".verdict").textContent).toBe("Pending");
      const briefing = screen.getByText(/Onboarding Assistant briefing — advisory/)
        .closest("details");
      expect(within(briefing).getByText(/derived from finding\s+severity/))
        .toBeInTheDocument();
    });

  it("omits the AI card entirely when the run produced no summary", async () => {
    api.run.mockResolvedValue({ ...RUN, ai_summary: null });
    renderRun();
    await loaded();
    expect(screen.queryByText(/Onboarding Assistant briefing/)).not.toBeInTheDocument();
  });

  it("flags ai_uncertain findings as reviewer attention, not as a failed check",
    async () => {
      renderRun();
      await loaded();
      expect(screen.getByText(/AI unsure — your judgement/i)).toBeInTheDocument();
    });
});

describe("polling", () => {
  it("stops once the run reports itself finished", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderRun();
    await loaded();
    const afterFirstLoad = api.run.mock.calls.length;

    await vi.advanceTimersByTimeAsync(6000);
    expect(api.run.mock.calls.length).toBe(afterFirstLoad);
    vi.useRealTimers();
  });

  it("keeps polling while the run is still going, and never on status alone",
    async () => {
      // PENDING is durable before the final stage runs, so `finished` — not the
      // status — is what decides whether there is more to come.
      vi.useFakeTimers({ shouldAdvanceTime: true });
      api.run.mockResolvedValue({ ...RUN, finished: false, status: "PENDING" });
      renderRun();
      // An unfinished run shows the live pipeline, not the decision.
      await screen.findByLabelText("Pipeline progress");
      const afterFirstLoad = api.run.mock.calls.length;

      await vi.advanceTimersByTimeAsync(4000);
      expect(api.run.mock.calls.length).toBeGreaterThan(afterFirstLoad);
      vi.useRealTimers();
    });
});

describe("while the run is executing", () => {
  const running = (stages) => ({
    ...RUN, finished: false, status: "RUNNING", status_label: "Processing",
    findings: [], checks: null,
    decision: { status: "RUNNING", status_label: "Processing",
                headline: "Checks in progress", summary: "", reason: "",
                next_action: "" },
    stages,
  });
  const at = (running_key) => [
    "intake", "completeness", "extraction", "format",
    "consistency", "decision", "review", "communicate",
  ].map((key, i, all) => ({
    key, label: key, ai: false, ms: null, detail: null, result: "",
    state: all.indexOf(running_key) > i ? "done"
      : key === running_key ? "running" : "pending",
  }));

  it("shows the five pipeline steps instead of an empty decision card",
    async () => {
      api.run.mockResolvedValue(running(at("extraction")));
      renderRun();
      const steps = await screen.findByLabelText("Pipeline progress");
      // The mark is a separate span, so read the label past it.
      expect([...steps.querySelectorAll("li")]
        .map((n) => n.lastChild.textContent.trim()))
        .toEqual(["Intake", "Extraction", "Validation", "Consistency", "Decision"]);
      expect(screen.queryByText("Checks in progress")).not.toBeInTheDocument();
    });

  it("marks the active step from the backend's own stage, and says what it is doing",
    async () => {
      api.run.mockResolvedValue(running(at("consistency")));
      renderRun();
      const steps = await screen.findByLabelText("Pipeline progress");
      const active = steps.querySelector('[aria-current="step"]');
      expect(active).toHaveTextContent("Consistency");
      expect(screen.getByText(/Cross-checking the submission/)).toBeInTheDocument();
    });

  it("treats a step as done only when every stage behind it is", async () => {
    // `completeness` is done but `format` has not run: Validation is not done.
    api.run.mockResolvedValue(running(at("extraction")));
    renderRun();
    const steps = await screen.findByLabelText("Pipeline progress");
    const [intake, , validation] = steps.querySelectorAll("li");
    expect(intake).toHaveClass("done");
    expect(validation).not.toHaveClass("done");
  });

  it("surfaces a failed stage rather than spinning forever", async () => {
    const stages = at("extraction").map((s) =>
      s.key === "extraction" ? { ...s, state: "failed" } : s);
    api.run.mockResolvedValue(running(stages));
    renderRun();
    const steps = await screen.findByLabelText("Pipeline progress");
    expect(steps.querySelector("li.failed")).toHaveTextContent("Extraction");
    expect(screen.getByText(/Extraction failed/)).toBeInTheDocument();
  });

  it("hands over to the decision view the moment the run finishes", async () => {
    renderRun();
    await loaded();
    expect(screen.queryByLabelText("Pipeline progress")).not.toBeInTheDocument();
  });
});

describe("closing the loop with the vendor", () => {
  it("lists exactly what the vendor has to fix, in plain instructions", async () => {
    renderRun();
    await loaded();
    const section = screen.getByText("Vendor correction").closest("section");
    expect(within(section).getByText(/Please upload your certificate of incorporation/))
      .toBeInTheDocument();
    // No rule identifiers reach the vendor-facing list.
    expect(section.textContent).not.toMatch(/\bR0[0-9]\b/);
  });

  it("says plainly where the case stands with the vendor", async () => {
    renderRun();
    await loaded();
    expect(screen.getByText("Corrections not requested yet")).toBeInTheDocument();
  });

  it("offers the link and nothing else — no email to write or send", async () => {
    renderRun();
    await loaded();
    const section = screen.getByText("Vendor correction").closest("section");
    expect(within(section).getByRole("button", { name: /open correction form/i }))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send/i })).not.toBeInTheDocument();
  });

  it("shows the case's existing link, before anything is opened", async () => {
    renderRun();
    await loaded();
    expect(screen.getByDisplayValue("http://localhost/vendor/onboard/tok123"))
      .toBeInTheDocument();
  });

  it("opens the form as an action only — no minting, no navigation", async () => {
    window.open = vi.fn();
    renderRun();
    await loaded();
    await userEvent.click(
      screen.getByRole("button", { name: /open correction form/i }));

    await waitFor(() => expect(api.reopenCase).toHaveBeenCalledWith("OC-1001"));
    // The reviewer stays on the run they were reading.
    expect(window.open).not.toHaveBeenCalled();
    // Same URL as before the click: the gate moved, the link did not.
    expect(screen.getByDisplayValue("http://localhost/vendor/onboard/tok123"))
      .toBeInTheDocument();
  });

  it("confirms the copy, so nobody wonders whether it worked", async () => {
    renderRun();
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: /copy link/i }));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("never shows a correction flow on a rejected run", async () => {
    api.run.mockResolvedValue({
      ...RUN,
      status: "REJECTED",
      status_label: "Rejected",
      decision: { ...RUN.decision, headline: "1 blocking inconsistency",
                  next_action: "Do not proceed with onboarding." },
      correction: { ...RUN.correction, awaiting_correction: false,
                    link_available: false, status_label: null, items: [] },
    });
    renderRun();
    await screen.findByText("1 blocking inconsistency");
    expect(screen.queryByText("Vendor correction")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open correction form/i }))
      .not.toBeInTheDocument();
  });

  it("keeps an approved run free of failure-oriented sections", async () => {
    api.run.mockResolvedValue({
      ...RUN,
      status: "APPROVED",
      status_label: "Approved",
      findings: [],
      decision: { status: "APPROVED", status_label: "Approved",
                  headline: "All required checks passed",
                  summary: "Everything present and consistent.", reason: "",
                  next_action: "No further action is required." },
      correction: { ...RUN.correction, awaiting_correction: false,
                    link_available: false, status_label: null, items: [] },
    });
    renderRun();
    await screen.findByText("All required checks passed");
    expect(screen.queryByText("Blocking issues")).not.toBeInTheDocument();
    expect(screen.queryByText("Needs correction")).not.toBeInTheDocument();
    expect(screen.queryByText("Vendor correction")).not.toBeInTheDocument();
    expect(screen.getByText(/No further action is required/)).toBeInTheDocument();
  });

  it("gives each submission its own tab, with decision and issue count",
    async () => {
      api.run.mockResolvedValue({
        ...RUN,
        rounds: [
          { round: 1, run_id: "VS-2001", status: "PENDING",
            status_label: "Pending", finding_count: 3, created_at: "Sep 5, 2026",
            current: true, latest: false },
          { round: 2, run_id: "VS-2002", status: "APPROVED",
            status_label: "Approved", finding_count: 0, created_at: "Sep 6, 2026",
            current: false, latest: true },
        ],
      });
      renderRun();
      await loaded();

      const tabs = screen.getByLabelText("Submissions");
      expect(within(tabs).getByText("Pending · 3 issues")).toBeInTheDocument();
      expect(within(tabs).getByText("Approved · 0 issues")).toBeInTheDocument();
      expect(within(tabs).getByText("Latest")).toBeInTheDocument();
      // The tab you are on is marked, and the others navigate.
      expect(within(tabs).getByRole("link", { name: /Submission 1/ }))
        .toHaveAttribute("aria-current", "page");
      expect(within(tabs).getByRole("link", { name: /Submission 2/ }))
        .toHaveAttribute("href", "/run/VS-2002");
    });

  it("shows the case's own status, not the status of the run being read",
    async () => {
      // Reading submission 1 (Pending) after the case was approved on 2.
      api.run.mockResolvedValue({
        ...RUN,
        case_status: { case_id: "OC-1001", status: "APPROVED",
                       status_label: "Approved", latest_run_id: "VS-2002" },
      });
      renderRun();
      await loaded();
      expect(screen.getByText("Approved")).toBeInTheDocument();
      expect(screen.getByText(/reading an earlier submission/i))
        .toBeInTheDocument();
    });
});

describe("failures", () => {
  it("explains a missing run instead of rendering an empty page", async () => {
    api.run.mockRejectedValue(Object.assign(new Error("gone"), { status: 404 }));
    renderRun();
    expect(await screen.findByText(/No run with id/)).toBeInTheDocument();
  });

  it("surfaces any other failure with a way to retry", async () => {
    api.run.mockRejectedValue(
      Object.assign(new Error("boom"), { detail: "Could not reach the server." }));
    renderRun();
    expect(await screen.findByRole("alert"))
      .toHaveTextContent("Could not reach the server.");
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
