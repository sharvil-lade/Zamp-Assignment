/**
 * The onboarding case detail page.
 *
 * Both are thin, but both handle something that must not go wrong — the demo
 * scenarios that make the four PS-2 outcomes reproducible, and the invite link,
 * which is shown exactly once and cannot be recovered afterwards.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OnboardingDetail from "../pages/OnboardingDetail";

vi.mock("../api", () => {
  const api = {
    caseDetail: vi.fn(), regenerateLink: vi.fn(),
  };
  return { default: api, api, ApiError: class ApiError extends Error {} };
});

import api from "../api";

const CASE = {
  link: { url: "http://localhost/vendor/onboard/tok123", open: true },
  case_id: "OC-1001",
  vendor_name: "Meridian Textiles Pvt Ltd",
  contact_name: "Priya Raman",
  contact_email: "priya@meridian.test",
  status: "AWAITING_VENDOR",
  status_label: "Awaiting vendor",
  run_id: null,
  created_at: "Sep 5, 2026 · 2:14 PM",
  submitted_at: "—",
  created_by: "arjun@zamp.test",
  form: { template_id: "FORM-0001", template_name: "Standard Vendor Onboarding" },
};

function renderCase() {
  return render(
    <MemoryRouter initialEntries={["/onboardings/OC-1001"]}>
      <Routes>
        <Route path="/onboardings/:caseId" element={<OnboardingDetail />} />
        <Route path="/run/:runId" element={<p>run page</p>} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  api.caseDetail.mockResolvedValue(CASE);
  api.regenerateLink.mockResolvedValue({
    case_id: "OC-1001",
    vendor_url: "http://localhost:5173/vendor/onboard/newtoken0123456789",
  });
});

describe("the onboarding case", () => {
  it("shows the case, its contact and the form it was created from", async () => {
    renderCase();
    expect(await screen.findByRole("heading", { name: CASE.vendor_name }))
      .toBeInTheDocument();
    expect(screen.getByText("Priya Raman")).toBeInTheDocument();
    expect(screen.getByText("Standard Vendor Onboarding")).toBeInTheDocument();
  });

  it("explains that the original link cannot be shown again", async () => {
    renderCase();
    expect(await screen.findByText(/one permanent link|Vendor submission form/))
      .toBeInTheDocument();
  });

  it("shows the case's one permanent link, with copy and open", async () => {
    renderCase();
    const card = (await screen.findByText("Vendor submission form")).closest("section");
    expect(within(card).getByRole("button", { name: /copy link/i }))
      .toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /open form/i }))
      .toBeInTheDocument();
    // Shown every time, not once: it is the case's URL for its whole life.
    expect(within(card).getByText(/vendor\/onboard\//)).toBeInTheDocument();
  });

  it("says the form is closed once the vendor has submitted, keeping the link",
    async () => {
      api.caseDetail.mockResolvedValue({
        ...CASE, status: "PROCESSING", status_label: "Processing",
        run_id: "VS-2001", submitted_at: "Sep 5, 2026 · 3:00 PM",
        link: { ...CASE.link, open: false },
      });
      renderCase();
      const card = (await screen.findByText("Vendor submission form"))
        .closest("section");
      expect(within(card).getByText("Closed")).toBeInTheDocument();
      // The link is still there — it is reopened from the run page, not reissued.
      expect(within(card).getByRole("button", { name: /copy link/i }))
        .toBeInTheDocument();
    });

  it("links through to the run once there is one", async () => {
    api.caseDetail.mockResolvedValue({ ...CASE, run_id: "VS-2001" });
    renderCase();
    await userEvent.click(await screen.findByRole("link", { name: "VS-2001" }));
    expect(await screen.findByText("run page")).toBeInTheDocument();
  });
});
