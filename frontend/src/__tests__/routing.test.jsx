/**
 * The route table, exercised through the real <App />.
 *
 * The split that matters is structural: the employee pages are nested inside
 * <Layout>, which gates on a session; the vendor portal and the login page are
 * not. These tests check that the wiring actually reflects it.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";

vi.mock("../api", () => {
  const noop = () => new Promise(() => {});   // never resolves: pages stay in loading
  const api = {
    session: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(() => Promise.resolve({ authenticated: false })),
    dashboard: noop, templates: noop, template: noop, caseDetail: noop,
    run: noop, vendorForm: noop,
  };
  return { default: api, api, ApiError: class ApiError extends Error {} };
});

import api from "../api";



function visit(path) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

beforeEach(() => {
  api.session.mockResolvedValue({ authenticated: false });
});

describe("signed out", () => {
  it.each([
    "/dashboard",
    "/onboardings/new",
    "/onboardings/OC-1001",
    "/forms",
    "/forms/FT-001",
    "/run/VS-2001",
  ])("sends %s to the login page", async (path) => {
    visit(path);
    expect(await screen.findByLabelText(/password/i)).toBeInTheDocument();
  });

  it("serves the vendor portal without a session, because a token is not a login",
    async () => {
      visit("/vendor/onboard/0123456789abcdef");
      expect(await screen.findByText(/Vendor registration/i)).toBeInTheDocument();
      expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    });

  it("never renders employee navigation on the vendor portal", async () => {
    visit("/vendor/onboard/0123456789abcdef");
    await screen.findByText(/Vendor registration/i);
    expect(screen.queryByRole("link", { name: /dashboard/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sign out/i })).not.toBeInTheDocument();
  });
});

describe("signed in", () => {
  beforeEach(() => {
    api.session.mockResolvedValue({ authenticated: true });
  });

  it("puts the employee shell around every internal page", async () => {
    visit("/dashboard");
    expect(await screen.findByRole("button", { name: /sign out/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^forms$/i })).toBeInTheDocument();
  });

  it("redirects the root to the dashboard", async () => {
    visit("/");
    await screen.findByRole("button", { name: /sign out/i });
    expect(window.location.pathname).toBe("/dashboard");
  });

  it("shows a not-found page for an unknown path rather than a blank screen",
    async () => {
      visit("/nowhere-at-all");
      expect(await screen.findByText(/couldn/i)).toBeInTheDocument();
    });

  it("still keeps the vendor portal outside the employee shell", async () => {
    visit("/vendor/onboard/0123456789abcdef");
    expect(await screen.findByText(/Vendor registration/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sign out/i })).not.toBeInTheDocument();
  });
});
