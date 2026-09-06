/**
 * The dashboard is the one screen the case study is judged on, and its metrics,
 * columns and meaning are fixed. These tests exist to catch a redesign that
 * quietly drops a column or changes what a number counts.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Dashboard from "../pages/Dashboard";

vi.mock("../api", () => {
  const api = { dashboard: vi.fn() };
  return { default: api, api, ApiError: class ApiError extends Error {} };
});

import api from "../api";

const PAYLOAD = {
  stats: { total: 4, approved: 1, pending: 2, rejected: 1, error: 0 },
  cases: [
    {
      case_id: "OC-1001", run_id: "VS-2001", vendor_name: "Sundaram Industrial Supplies LLP",
      status: "APPROVED", status_label: "Approved", awaiting: false,
      stage: "Decision", finding_count: 0,
      created_at: "Sep 5, 2026 · 2:14 PM", created_at_iso: "2026-09-05T14:14:07+00:00",
      last_activity_at: "Sep 5, 2026 · 2:15 PM",
      last_activity_at_iso: "2026-09-05T14:15:00+00:00",
      last_activity_label: "Processing complete",
    },
    {
      case_id: "OC-1002", run_id: null, vendor_name: "Meridian Textiles Pvt Ltd",
      status: "AWAITING_VENDOR", status_label: "Awaiting vendor", awaiting: true,
      stage: null, finding_count: null,
      created_at: "Sep 5, 2026 · 3:00 PM", created_at_iso: "2026-09-05T15:00:00+00:00",
      last_activity_at: "Sep 5, 2026 · 3:00 PM",
      last_activity_at_iso: "2026-09-05T15:00:00+00:00",
      last_activity_label: "Case created",
    },
  ],
  runs: [
    { run_id: "VS-2001", case_id: "OC-1001", submission_no: 1, is_latest: false,
      vendor_name: "Sundaram Industrial Supplies LLP",
      status: "PENDING", status_label: "Pending", finding_count: 3,
      created_at: "Sep 5, 2026 · 2:14 PM", duration_ms: 1200 },
    { run_id: "VS-2002", case_id: "OC-1001", submission_no: 2, is_latest: true,
      vendor_name: "Sundaram Industrial Supplies LLP",
      status: "APPROVED", status_label: "Approved", finding_count: 0,
      created_at: "Sep 6, 2026 · 9:02 AM", duration_ms: 1840 },
  ],
  statuses: ["APPROVED", "PENDING", "REJECTED", "ERROR"],
  status_labels: { APPROVED: "Approved", PENDING: "Pending",
                   REJECTED: "Rejected", ERROR: "Error" },
  active: null,
};

function renderDashboard(entry = "/dashboard") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/run/:runId" element={<p>run page</p>} />
        <Route path="/onboardings/:caseId" element={<p>case page</p>} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  api.dashboard.mockImplementation((status) =>
    Promise.resolve({ ...PAYLOAD, active: status ?? null }));
});

describe("metrics", () => {
  it("shows total, approved, pending and rejected — the four that were always there",
    async () => {
      renderDashboard();
      await screen.findByText("Dashboard");
      for (const label of ["Total", "Approved", "Pending", "Rejected"]) {
        expect(screen.getAllByText(label).length).toBeGreaterThan(0);
      }
      expect(screen.getByText("4")).toBeInTheDocument();
    });
});

describe("the case table", () => {
  it("keeps every column", async () => {
    renderDashboard();
    await screen.findByText("Onboarding Cases");
    const table = screen.getByText("Onboarding Cases").closest("section");
    for (const heading of ["Case", "Vendor", "Status", "Findings",
                           "Created", "Last activity"]) {
      expect(within(table).getByText(heading)).toBeInTheDocument();
    }
  });

  it("shows a case still waiting on its vendor without inventing a finding count",
    async () => {
      renderDashboard();
      const row = (await screen.findByText("Meridian Textiles Pvt Ltd")).closest("tr");
      expect(within(row).getByText("Awaiting vendor")).toBeInTheDocument();
      expect(within(row).getAllByText("—").length).toBe(1);   // findings only
    });

  it("opens the run when a processed case is clicked", async () => {
    renderDashboard();
    // The vendor appears in both tables; this assertion is about the case one.
    const cases = (await screen.findByText("Onboarding Cases")).closest("section");
    await userEvent.click(
      within(cases).getByText("Sundaram Industrial Supplies LLP").closest("tr"));
    expect(await screen.findByText("run page")).toBeInTheDocument();
  });

  it("opens the case when it has no run yet, because there is nothing to show",
    async () => {
      renderDashboard();
      const row = (await screen.findByText("Meridian Textiles Pvt Ltd")).closest("tr");
      await userEvent.click(row);
      expect(await screen.findByText("case page")).toBeInTheDocument();
    });
});

describe("the status filter", () => {
  it("asks the server for the chosen status and narrows the list to it",
    async () => {
      renderDashboard();
      const rows = () => within(screen.getByText("Onboarding Cases")
        .closest("section")).getAllByRole("row").length;
      await screen.findByText("Onboarding Cases");
      const before = rows();

      await userEvent.click(screen.getByRole("button", { name: /Approved/ }));
      await waitFor(() => expect(api.dashboard).toHaveBeenLastCalledWith("APPROVED"));
      await waitFor(() => expect(rows()).toBeLessThan(before));
    });

  it("counts vendors awaiting a submission from the rows it already has",
    async () => {
      renderDashboard();
      await screen.findByText("Onboarding Cases");
      expect(screen.getByRole("button", { name: /Awaiting vendor/ }))
        .toBeInTheDocument();
    });
});

describe("the dashboard cannot destroy anything", () => {
  it("offers no way to wipe the data", async () => {
    renderDashboard();
    await screen.findByText("Dashboard");
    expect(screen.queryByRole("button", { name: /reset/i })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/reset|wipe|delete every/i);
  });
});

describe("failures", () => {
  it("shows the server's message instead of an empty screen", async () => {
    api.dashboard.mockRejectedValue(
      Object.assign(new Error("boom"), { detail: "Could not reach the server." }));
    renderDashboard();
    expect(await screen.findByRole("alert"))
      .toHaveTextContent("Could not reach the server.");
  });

  it("offers an empty state rather than a bare table when there is nothing yet",
    async () => {
      api.dashboard.mockResolvedValue({
        ...PAYLOAD, cases: [], runs: [],
        stats: { total: 0, approved: 0, pending: 0, rejected: 0, error: 0 },
      });
      renderDashboard();
      expect(await screen.findByText(/No onboarding cases yet/)).toBeInTheDocument();
    });
});
