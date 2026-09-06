/**
 * Authentication in the browser.
 *
 * One shared password guards the employee side, so there is nothing to know
 * about *who* is signed in — only whether. The credential itself is a signed
 * session token the API client holds and sends as `Authorization: Bearer`; what
 * is tested here is what React does with the answer to "am I signed in".
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Layout from "../components/Layout";
import { SessionProvider } from "../hooks/useSession";
import Login from "../pages/Login";

vi.mock("../api", () => {
  const api = {
    session: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(() => Promise.resolve({ authenticated: false })),
  };
  return { default: api, api, ApiError: class ApiError extends Error {} };
});

import api from "../api";

function renderAt(path, element, extra = null) {
  return render(
    <SessionProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={path} element={element} />
          {extra}
          <Route path="/login" element={<p>login page</p>} />
          <Route path="/dashboard" element={<p>dashboard page</p>} />
        </Routes>
      </MemoryRouter>
    </SessionProvider>
  );
}

function renderLogin() {
  return render(
    <SessionProvider>
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    </SessionProvider>
  );
}

beforeEach(() => {
  api.session.mockResolvedValue({ authenticated: false });
  api.login.mockReset();
});

describe("the login page", () => {
  it("asks for a password and nothing else", async () => {
    renderLogin();
    expect(await screen.findByLabelText(/password/i)).toBeInTheDocument();
    // No email, no username, no sign-up, no reset: this is a door.
    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/sign up|create an account|forgot/i))
      .not.toBeInTheDocument();
  });

  it("sends only the password to the API", async () => {
    api.login.mockResolvedValue({});
    renderLogin();
    await userEvent.type(await screen.findByLabelText(/password/i), "gozamp");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(api.login).toHaveBeenCalledWith("gozamp"));
  });

  it("shows the server's message and stays put when the password is rejected",
    async () => {
      const error = new Error("rejected");
      error.detail = "That password was not recognised.";
      api.login.mockRejectedValue(error);

      renderLogin();
      await userEvent.type(await screen.findByLabelText(/password/i), "wrong");
      await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

      expect(await screen.findByRole("alert"))
        .toHaveTextContent("That password was not recognised.");
      expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    });

  it("never echoes the attempted password back onto the screen", async () => {
    const error = new Error("rejected");
    error.detail = "That password was not recognised.";
    api.login.mockRejectedValue(error);

    renderLogin();
    await userEvent.type(await screen.findByLabelText(/password/i), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(/hunter2/);
  });
});

describe("the employee shell", () => {
  it("sends an anonymous visitor to the login page", async () => {
    renderAt("/dashboard-protected",
      <Layout />,
      <Route path="/dashboard-protected" element={<Layout />} />);
    expect(await screen.findByText("login page")).toBeInTheDocument();
  });

  it("renders the navigation once there is a session", async () => {
    api.session.mockResolvedValue({ authenticated: true });
    render(
      <SessionProvider>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<p>dashboard page</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </SessionProvider>
    );

    expect(await screen.findByRole("link", { name: /^forms$/i })).toBeInTheDocument();
    expect(screen.getByText("dashboard page")).toBeInTheDocument();
    // Nothing claims to know who this is, because nothing does.
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
  });

  it("clears the session on sign out and falls back to the login page", async () => {
    api.session.mockResolvedValue({ authenticated: true });
    render(
      <SessionProvider>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<p>dashboard page</p>} />
            </Route>
            <Route path="/login" element={<p>login page</p>} />
          </Routes>
        </MemoryRouter>
      </SessionProvider>
    );

    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(api.logout).toHaveBeenCalled());
    expect(await screen.findByText("login page")).toBeInTheDocument();
  });

  it("treats a failed session check as signed out rather than crashing", async () => {
    api.session.mockRejectedValue(new Error("network down"));
    renderAt("/dashboard-protected",
      <Layout />,
      <Route path="/dashboard-protected" element={<Layout />} />);
    expect(await screen.findByText("login page")).toBeInTheDocument();
  });
});
