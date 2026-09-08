/**
 * The vendor portal is a security boundary, not just another page.
 *
 * A vendor may see the form they were invited to fill in and confirmation that
 * it arrived. Never a decision, a finding, a risk level, AI reasoning, a rule
 * id, an internal note, an audit event, a run or case id, or a link into the
 * employee app. These tests exist to keep that true as the UI changes.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VendorForm from "../pages/VendorForm";

vi.mock("../api", () => {
  const api = { vendorForm: vi.fn(), vendorSubmit: vi.fn() };
  return { default: api, api, ApiError: class ApiError extends Error {} };
});

import api from "../api";

const TOKEN = "0123456789abcdef0123456789abcdef";

const OPEN = {
  state: "open",
  vendor_name: "Sundaram Industrial Supplies LLP",
  contact_name: "Priya Raman",
  schema: {
    sections: [
      {
        title: "Company information",
        fields: [
          { id: "legal_entity_name", label: "Legal entity name", type: "text",
            required: true, canonical: "legal_entity_name" },
          { id: "entity_type", label: "Entity type", type: "select", required: true,
            canonical: "entity_type", options: ["Private Limited", "LLP"] },
          { id: "gstin", label: "GSTIN", type: "text", required: true,
            canonical: "gstin" },
          { id: "trading_since", label: "Trading since", type: "date", required: true },
        ],
      },
      {
        title: "Required documents",
        fields: [
          { id: "bank_proof", label: "Cancelled cheque or bank letter",
            type: "document", required: true, canonical: "bank_proof" },
        ],
      },
    ],
  },
  prefill: {
    legal_entity_name: "Sundaram Industrial Supplies LLP",
    contact_name: "Priya Raman",
    contact_email: "priya@sundaram.test",
  },
  max_upload_mb: 10,
  accepted_types: [".jpg", ".pdf", ".png"],
};

function renderVendor() {
  return render(
    <MemoryRouter initialEntries={[`/vendor/onboard/${TOKEN}`]}>
      <Routes>
        <Route path="/vendor/onboard/:token" element={<VendorForm />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  api.vendorForm.mockResolvedValue(OPEN);
  api.vendorSubmit.mockResolvedValue({ state: "received" });
});

/** Fill in the form the way a vendor would, then submit it.
 *
 * The submit is dispatched on the form rather than by clicking the button:
 * jsdom does not implement the implicit-submission algorithm, so a click on a
 * submit button never reaches React. What is under test is the handler.
 */
async function completeAndSubmit() {
  const entityType = await screen.findByLabelText(/entity type/i);
  await userEvent.selectOptions(entityType, "LLP");
  await userEvent.type(screen.getByLabelText(/gstin/i), "29ABCFS1234K1Z3");
  await userEvent.type(screen.getByLabelText(/trading since/i), "2019-04-01");
  await userEvent.upload(
    screen.getByLabelText(/cancelled cheque/i),
    new File(["%PDF-1.4"], "cheque.pdf", { type: "application/pdf" }));
  fireEvent.submit(entityType.closest("form"));
}

describe("rendering the invited form", () => {
  it("renders every field the schema defines, including custom ones", async () => {
    renderVendor();
    expect(await screen.findByLabelText(/legal entity name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/entity type/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/gstin/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/trading since/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/cancelled cheque/i)).toBeInTheDocument();
  });

  it("renders a select as a select and a date as a date, from the schema alone",
    async () => {
      renderVendor();
      const entityType = await screen.findByLabelText(/entity type/i);
      expect(entityType.tagName).toBe("SELECT");
      expect(screen.getByLabelText(/trading since/i)).toHaveAttribute("type", "date");
      expect(screen.getByLabelText(/cancelled cheque/i)).toHaveAttribute("type", "file");
    });

  it("prefills what the employee already knew, so the vendor does not retype it",
    async () => {
      renderVendor();
      expect(await screen.findByLabelText(/legal entity name/i))
        .toHaveValue("Sundaram Industrial Supplies LLP");
    });

  it("states the upload limit and the accepted types rather than hardcoding them",
    async () => {
      renderVendor();
      expect(await screen.findByText(/up to 10 MB per file/i)).toBeInTheDocument();
      expect(screen.getByText(/\.pdf/)).toBeInTheDocument();
    });
});

describe("strict submission", () => {
  it("sends nothing while a required field or document is missing", async () => {
    renderVendor();
    const entityType = await screen.findByLabelText(/entity type/i);
    // Everything except the document, which is the easiest thing to forget.
    await userEvent.selectOptions(entityType, "LLP");
    await userEvent.type(screen.getByLabelText(/gstin/i), "29ABCFS1234K1Z3");
    await userEvent.type(screen.getByLabelText(/trading since/i), "2019-04-01");
    fireEvent.submit(entityType.closest("form"));

    expect(api.vendorSubmit).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(/still needed/i);
    expect(screen.getByRole("alert"))
      .toHaveTextContent(/cancelled cheque or bank letter/i);
  });

  it("does not demand a document the case already holds", async () => {
    // A correction round: the bank letter is on file, so the vendor fixes what
    // was wrong instead of re-attaching four good files.
    api.vendorForm.mockResolvedValue({ ...OPEN, correcting: true,
                                       on_file: ["bank_proof"] });
    renderVendor();
    const entityType = await screen.findByLabelText(/entity type/i);
    await userEvent.selectOptions(entityType, "LLP");
    await userEvent.type(screen.getByLabelText(/gstin/i), "29ABCFS1234K1Z3");
    await userEvent.type(screen.getByLabelText(/trading since/i), "2019-04-01");
    fireEvent.submit(entityType.closest("form"));

    expect(await screen.findByText(/your details are with the onboarding team/i))
      .toBeInTheDocument();
  });
});

describe("submitting", () => {
  it("posts multipart form data addressed only by token", async () => {
    renderVendor();
    await completeAndSubmit();

    const [token, body] = api.vendorSubmit.mock.calls[0];
    expect(token).toBe(TOKEN);
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("legal_entity_name")).toBe("Sundaram Industrial Supplies LLP");
    expect(body.get("trading_since")).not.toBeNull();
  });

  it("confirms receipt without a status, a timeline or anything to check later",
    async () => {
      renderVendor();
      await completeAndSubmit();

      expect(await screen.findByText(/your details are with the onboarding team/i))
        .toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /submit/i })).not.toBeInTheDocument();
    });

  it("treats a replayed link as already received rather than as an error", async () => {
    api.vendorSubmit.mockRejectedValue(Object.assign(new Error("used"), { status: 409 }));
    renderVendor();
    await completeAndSubmit();
    expect(await screen.findByText(/your details are with the onboarding team/i))
      .toBeInTheDocument();
  });

  it("shows the confirmation, not the form, for a case that was already submitted",
    async () => {
      api.vendorForm.mockResolvedValue({
        state: "submitted", vendor_name: "Sundaram Industrial Supplies LLP",
        contact_name: "Priya Raman",
      });
      renderVendor();
      expect(await screen.findByText(/your details are with the onboarding team/i))
        .toBeInTheDocument();
      expect(screen.queryByLabelText(/gstin/i)).not.toBeInTheDocument();
    });
});

describe("a bad link", () => {
  it("says only that it is not valid, never why", async () => {
    api.vendorForm.mockRejectedValue(
      Object.assign(new Error("nope"),
                    { status: 404, detail: "This onboarding link is not valid." }));
    renderVendor();

    expect(await screen.findByText(/this onboarding link is not valid/i))
      .toBeInTheDocument();
    expect(document.body.textContent)
      .not.toMatch(/expired|already used|no such case|does not exist|unknown token/i);
  });
});

describe("isolation from the employee application", () => {
  async function bodyText() {
    renderVendor();
    await screen.findByLabelText(/legal entity name/i);
    return document.body.textContent;
  }

  it("leaks no internal vocabulary", async () => {
    const text = await bodyText();
    for (const leak of ["Decision Engine", "finding", "Finding", "APPROVED",
                        "REJECTED", "PENDING", "risk", "Risk", "rule", "Rule",
                        "audit", "Audit", "internal note", "Onboarding Assistant"]) {
      expect(text).not.toContain(leak);
    }
  });

  it("offers no route into the employee application", async () => {
    renderVendor();
    await screen.findByLabelText(/legal entity name/i);
    const targets = [...document.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    for (const href of targets) {
      expect(href).not.toMatch(/\/dashboard|\/run\/|\/login|\/reset|\/onboardings|\/forms/);
    }
  });

  it("shows no case id, run id or employee identity", async () => {
    const text = await bodyText();
    expect(text).not.toMatch(/\bOC-\d|\bVS-\d/);
    expect(text).not.toMatch(/@zamp\.test/);
  });

  it("never sends anything but the token as authorisation", async () => {
    renderVendor();
    await completeAndSubmit();
    const body = api.vendorSubmit.mock.calls[0][1];
    expect(body.get("case_id")).toBeNull();
    expect(body.get("run_id")).toBeNull();
  });
});
