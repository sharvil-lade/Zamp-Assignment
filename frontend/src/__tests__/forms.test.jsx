/**
 * Configurable forms. A form is a form: the list creates, copies and deletes,
 * the builder edits one in place with a single Save. There are no versions, no
 * drafts and no publishing — what protects a vendor mid-onboarding is the schema
 * snapshot the server takes on each case, not anything this page does.
 *
 * The distinction the page really has to teach is canonical versus custom: it
 * decides whether the deterministic rule engine ever looks at a field.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FormBuilder from "../pages/FormBuilder";
import FormTemplates from "../pages/FormTemplates";

vi.mock("../api", () => {
  const api = {
    templates: vi.fn(),
    template: vi.fn(),
    createTemplate: vi.fn(),
    updateTemplate: vi.fn(),
    duplicateTemplate: vi.fn(),
    deleteTemplate: vi.fn(),
  };
  return { default: api, api, ApiError: class ApiError extends Error {} };
});

import api from "../api";

const STANDARD = {
  id: "FORM-0001", name: "Standard Vendor Onboarding",
  description: "The default PS-2 onboarding form.", created_by: "system",
  created_at: "Sep 1, 2026 · 9:00 AM", updated_at: "Sep 1, 2026 · 9:00 AM",
  field_count: 14, document_count: 5, is_standard: true,
};

const CUSTOM = {
  id: "FORM-0002", name: "International Vendor Onboarding",
  description: "For vendors outside India.", created_by: "arjun@zamp.test",
  created_at: "Sep 4, 2026 · 11:00 AM", updated_at: "Sep 5, 2026 · 4:30 PM",
  field_count: 2, document_count: 0, is_standard: false,
};

const SCHEMA = {
  sections: [
    {
      title: "About you",
      fields: [
        { id: "legal_entity_name", label: "Legal entity name", type: "text",
          required: true, canonical: "legal_entity_name" },
        { id: "trading_since", label: "Trading since", type: "date", required: true },
      ],
    },
  ],
};

const META = {
  templates: [STANDARD, CUSTOM],
  field_types: ["text", "textarea", "email", "phone", "number", "date",
                "boolean", "select", "document"],
  canonical_fields: ["gstin", "legal_entity_name", "pan"],
  // extract.DOC_TYPES, verbatim. The builder may only offer canonicals the
  // server recognises, so this list drifting is the bug the tests below catch.
  document_types: ["pan_card", "gst_certificate", "incorporation_certificate",
                   "bank_proof", "address_proof"],
};

/** The builder payload: one form, one schema, no versions array. */
const detail = (over = {}) => ({ template: { ...CUSTOM, schema: SCHEMA, ...over } });

function renderTemplates() {
  return render(
    <MemoryRouter initialEntries={["/forms"]}>
      <Routes>
        <Route path="/forms" element={<FormTemplates />} />
        <Route path="/forms/:templateId" element={<p>builder page</p>} />
      </Routes>
    </MemoryRouter>
  );
}

function renderBuilder(entry = "/forms/FORM-0002") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/forms/:templateId" element={<FormBuilder />} />
        <Route path="/forms" element={<p>templates page</p>} />
      </Routes>
    </MemoryRouter>
  );
}

const rowFor = (name) => screen.getByText(name).closest("tr");

beforeEach(() => {
  api.templates.mockResolvedValue(META);
  api.template.mockResolvedValue(detail());
  api.updateTemplate.mockResolvedValue({ ok: true });
  api.createTemplate.mockResolvedValue({ template_id: "FORM-0003" });
  api.duplicateTemplate.mockResolvedValue({ template_id: "FORM-0004" });
  api.deleteTemplate.mockResolvedValue({ ok: true });
  window.confirm = () => true;
  window.prompt = () => "Copy of International";
});

describe("the form list", () => {
  it("lists every form with what it asks for", async () => {
    renderTemplates();
    expect(await screen.findByText("International Vendor Onboarding"))
      .toBeInTheDocument();
    expect(screen.getByText("Standard Vendor Onboarding")).toBeInTheDocument();
    expect(within(rowFor("Standard Vendor Onboarding")).getByText("14"))
      .toBeInTheDocument();
  });

  it("shows the seeded standard form as the default", async () => {
    renderTemplates();
    await screen.findByText("Standard Vendor Onboarding");
    expect(within(rowFor("Standard Vendor Onboarding")).getAllByText(/default/i).length)
      .toBeGreaterThan(0);
  });

  it("creates an empty form and opens the builder on it", async () => {
    renderTemplates();
    await screen.findByText("International Vendor Onboarding");
    await userEvent.click(screen.getByRole("button", { name: /new form/i }));
    await userEvent.type(screen.getByLabelText(/name/i), "EU Vendors");
    await userEvent.click(screen.getByRole("button", { name: /create and edit/i }));

    await waitFor(() => expect(api.createTemplate).toHaveBeenCalled());
    // No schema is sent: the questions are added in the builder, not here.
    expect(api.createTemplate.mock.calls[0][0]).toEqual({
      name: "EU Vendors", description: "",
    });
    expect(await screen.findByText("builder page")).toBeInTheDocument();
  });

  it("copies a form under a new name and opens the copy", async () => {
    renderTemplates();
    await screen.findByText("International Vendor Onboarding");
    await userEvent.click(
      within(rowFor("International Vendor Onboarding"))
        .getByRole("button", { name: /duplicate/i }));

    await waitFor(() => expect(api.duplicateTemplate)
      .toHaveBeenCalledWith("FORM-0002", "Copy of International"));
    expect(await screen.findByText("builder page")).toBeInTheDocument();
  });

  it("deletes a form and reloads the list", async () => {
    renderTemplates();
    await screen.findByText("International Vendor Onboarding");
    await userEvent.click(
      within(rowFor("International Vendor Onboarding"))
        .getByRole("button", { name: /delete/i }));

    await waitFor(() => expect(api.deleteTemplate).toHaveBeenCalledWith("FORM-0002"));
    expect(api.templates).toHaveBeenCalledTimes(2);
  });

  it("never offers to delete the standard form", async () => {
    renderTemplates();
    await screen.findByText("Standard Vendor Onboarding");
    expect(within(rowFor("Standard Vendor Onboarding"))
      .queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it("shows the server's refusal when a form is already in use", async () => {
    // The server decides: a form an onboarding was created from cannot go, and
    // the page reports that reason rather than guessing which deletes are legal.
    api.deleteTemplate.mockRejectedValue(Object.assign(new Error("conflict"), {
      status: 409,
      detail: "Onboardings were created from this form, so it cannot be deleted.",
    }));
    renderTemplates();
    await screen.findByText("International Vendor Onboarding");
    await userEvent.click(
      within(rowFor("International Vendor Onboarding"))
        .getByRole("button", { name: /delete/i }));

    expect(await screen.findByRole("alert"))
      .toHaveTextContent(/Onboardings were created from this form/);
  });

  it("does not confirm a delete the employee cancelled", async () => {
    window.confirm = () => false;
    renderTemplates();
    await screen.findByText("International Vendor Onboarding");
    await userEvent.click(
      within(rowFor("International Vendor Onboarding"))
        .getByRole("button", { name: /delete/i }));
    expect(api.deleteTemplate).not.toHaveBeenCalled();
  });
});

/** Open a question card the way an author does: by clicking it. */
async function openField(label) {
  await userEvent.click(await screen.findByText(label));
}


describe("editing a form in place", () => {
  it("loads the form's schema into the editor", async () => {
    renderBuilder();
    expect(await screen.findByDisplayValue("About you")).toBeInTheDocument();
    // Closed cards show the question, not an input for it.
    expect(screen.getByText("Trading since")).toBeInTheDocument();
    await openField("Trading since");
    expect(screen.getByDisplayValue("Trading since")).toBeInTheDocument();
  });

  it("saves the edited schema over the form, not as a copy of it", async () => {
    renderBuilder();
    const title = await screen.findByDisplayValue("About you");
    await userEvent.clear(title);
    await userEvent.type(title, "Your company");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(api.updateTemplate).toHaveBeenCalled());
    const [formId, body] = api.updateTemplate.mock.calls[0];
    expect(formId).toBe("FORM-0002");
    expect(body.form_schema.sections[0].title).toBe("Your company");
    expect(body.name).toBe("International Vendor Onboarding");
  });

  it("saves a renamed form without losing the schema", async () => {
    renderBuilder();
    const name = await screen.findByLabelText(/form name/i);
    await userEvent.clear(name);
    await userEvent.type(name, "EU Vendor Onboarding");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(api.updateTemplate).toHaveBeenCalled());
    const [, body] = api.updateTemplate.mock.calls[0];
    expect(body.name).toBe("EU Vendor Onboarding");
    expect(body.form_schema.sections[0].fields).toHaveLength(2);
  });

  it("adds a section without touching the existing one", async () => {
    renderBuilder();
    await screen.findByDisplayValue("About you");
    await userEvent.click(screen.getByRole("button", { name: /add section/i }));
    expect(screen.getByDisplayValue("New section")).toBeInTheDocument();
    expect(screen.getByDisplayValue("About you")).toBeInTheDocument();
  });

  it("shows the server's refusal rather than claiming it saved", async () => {
    api.updateTemplate.mockRejectedValue(Object.assign(new Error("bad"), {
      status: 400, detail: "select field 'a' needs at least one option" }));
    renderBuilder();
    await screen.findByDisplayValue("About you");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByRole("alert"))
      .toHaveTextContent("select field 'a' needs at least one option");
    expect(screen.queryByText(/^Saved\.$/)).not.toBeInTheDocument();
  });

  it("offers one save and nothing to publish or branch", async () => {
    // Guards the removal: a publish button or a version picker reappearing would
    // mean an edit no longer takes effect on the next onboarding.
    renderBuilder();
    await screen.findByDisplayValue("About you");
    expect(screen.queryByRole("button", { name: /publish/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/version/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/read-only/i)).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("About you")).toBeEnabled();
  });
});

describe("canonical fields versus custom fields", () => {
  it("explains the difference, because it decides whether a rule ever runs",
    async () => {
      renderBuilder();
      await openField("Trading since");
      expect(screen.getByText(/Canonical fields versus custom fields/i))
        .toBeInTheDocument();
    });

  it("offers only canonicals the server recognises, values and documents alike",
    async () => {
      renderBuilder();
      await openField("Trading since");
      const options = [...document.querySelectorAll("option")].map((o) => o.value);
      for (const canonical of [...META.canonical_fields, ...META.document_types]) {
        expect(options).toContain(canonical);
      }
    });

  it("says which of the two a field is, on the field itself", async () => {
    renderBuilder();
    await openField("Trading since");        // custom: nothing maps to it
    expect(screen.getByText(/No business rules/i)).toBeInTheDocument();

    await openField("Legal entity name");    // canonical: the engine reads it
    expect(screen.getByText(/Read by the rule engine as/i)).toBeInTheDocument();
  });

  it("shows a canonical mapping on the closed card, so it is visible at a glance",
    async () => {
      renderBuilder();
      await screen.findByDisplayValue("About you");
      expect(screen.getByText("legal_entity_name")).toBeInTheDocument();
    });
});

describe("the two tabs", () => {
  it("opens on the editor", async () => {
    renderBuilder();
    await screen.findByDisplayValue("About you");
    expect(screen.getByRole("tab", { name: /edit/i }))
      .toHaveAttribute("aria-selected", "true");
  });

  it("shows the vendor's view without any editing controls", async () => {
    renderBuilder();
    await screen.findByDisplayValue("About you");
    await userEvent.click(screen.getByRole("tab", { name: /form view/i }));

    expect(screen.getByLabelText(/legal entity name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/trading since/i)).toHaveAttribute("type", "date");
    expect(screen.queryByRole("button", { name: /add section/i })).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("About you")).not.toBeInTheDocument();
  });

  it("keeps unsaved edits when switching tabs", async () => {
    renderBuilder();
    const title = await screen.findByDisplayValue("About you");
    await userEvent.clear(title);
    await userEvent.type(title, "Your company");

    await userEvent.click(screen.getByRole("tab", { name: /form view/i }));
    expect(screen.getByText("Your company")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /edit/i }));
    expect(screen.getByDisplayValue("Your company")).toBeInTheDocument();
  });
});

describe("client-side validation mirrors the server without replacing it", () => {
  it("names a duplicate field id before a save is even attempted", async () => {
    api.template.mockResolvedValue(detail({
      schema: { sections: [{ title: "A", fields: [
        { id: "a", label: "A", type: "text" },
        { id: "a", label: "B", type: "text" },
      ] }] },
    }));
    renderBuilder();
    expect(await screen.findByText(/duplicate field id/i)).toBeInTheDocument();
    expect(api.updateTemplate).not.toHaveBeenCalled();
  });

  it("names a select with no options", async () => {
    api.template.mockResolvedValue(detail({
      schema: { sections: [{ title: "A", fields: [
        { id: "a", label: "A", type: "select" },
      ] }] },
    }));
    renderBuilder();
    // Scoped to the warning banner: "options" is also an editor field label.
    const banner = (await screen.findByText(/cannot be saved yet/i)).closest(".alert");
    expect(within(banner).getByText(/needs at least one option/i)).toBeInTheDocument();
  });
});
