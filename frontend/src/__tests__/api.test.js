/**
 * The API client is the only place this app talks to the server, so the things
 * that must never regress — credentials, error shape, base path — are tested
 * here once rather than in every page.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import api, { ApiError, getToken, setToken } from "../api";

function respond(body, { ok = true, status = 200 } = {}) {
  return Promise.resolve({
    ok,
    status,
    statusText: "",
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => {
  global.fetch = vi.fn(() => respond({}));
  setToken(null);
});

describe("request shaping", () => {
  it("sends the access token as a bearer credential once signed in", async () => {
    global.fetch = vi.fn(() => respond({ access_token: "1799999999.abcdef",
                                         token_type: "bearer", expires_in: 43200 }));
    await api.login("secret");

    global.fetch = vi.fn(() => respond({}));
    await api.dashboard();
    const [, init] = global.fetch.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer 1799999999.abcdef");
  });

  it("sends no Authorization header when nobody is signed in", async () => {
    await api.dashboard();
    const [, init] = global.fetch.mock.calls[0];
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("never relies on an ambient cookie", async () => {
    await api.dashboard();
    const [, init] = global.fetch.mock.calls[0];
    expect(init.credentials).toBeUndefined();
  });

  it("prefixes every path with /api", async () => {
    await api.dashboard();
    expect(global.fetch.mock.calls[0][0]).toMatch(/\/api\/dashboard$/);
  });

  it("passes a status filter through as a query parameter", async () => {
    await api.dashboard("REJECTED");
    expect(global.fetch.mock.calls[0][0]).toMatch(/\/api\/dashboard\?status=REJECTED$/);
  });

  it("serialises a JSON body and sets the content type", async () => {
    await api.login("secret");
    const [, init] = global.fetch.mock.calls[0];
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ password: "secret" });
  });

  it("leaves the content type alone for multipart, so the browser sets the boundary",
    async () => {
      await api.vendorSubmit("tok", new FormData());
      const [, init] = global.fetch.mock.calls[0];
      expect(init.headers["Content-Type"]).toBeUndefined();
      expect(init.body).toBeInstanceOf(FormData);
    });
});

describe("the token", () => {
  it("survives a reload, which is why it is stored rather than held in memory",
    async () => {
      global.fetch = vi.fn(() => respond({ access_token: "1799999999.stored",
                                           token_type: "bearer", expires_in: 1 }));
      await api.login("secret");
      expect(localStorage.getItem("vo_access_token")).toBe("1799999999.stored");
    });

  it("is discarded on sign out, because a stateless token cannot be revoked",
    async () => {
      setToken("1799999999.abc");
      api.logout();
      expect(getToken()).toBeNull();
      expect(localStorage.getItem("vo_access_token")).toBeNull();
    });

  it("is dropped the moment the server rejects it", async () => {
    setToken("1000000000.expired");
    global.fetch = vi.fn(() =>
      respond({ detail: "authentication required" }, { ok: false, status: 401 }));

    await api.dashboard().catch(() => {});
    expect(getToken()).toBeNull();
  });
});

describe("failures", () => {
  it("turns a FastAPI detail into an ApiError carrying the status", async () => {
    global.fetch = vi.fn(() =>
      respond({ detail: "Those credentials were not recognised." },
              { ok: false, status: 401 }));

    const error = await api.login("a@b.test", "nope").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(401);
    expect(error.detail).toBe("Those credentials were not recognised.");
  });

  it("flattens a 422 validation list into one readable message", async () => {
    global.fetch = vi.fn(() =>
      respond({ detail: [{ msg: "field required" }, { msg: "not a valid email" }] },
              { ok: false, status: 422 }));

    const error = await api.createCase({}).catch((e) => e);
    expect(error.detail).toBe("field required; not a valid email");
  });

  it("reports an unreachable backend rather than throwing a fetch error", async () => {
    global.fetch = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));

    const error = await api.dashboard().catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.detail).toMatch(/Could not reach the server/);
  });

  it("survives an error response with no JSON body", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false, status: 500, statusText: "Internal Server Error",
        json: () => Promise.reject(new Error("not json")),
      }));

    const error = await api.dashboard().catch((e) => e);
    expect(error.status).toBe(500);
    expect(error.detail).toBe("Internal Server Error");
  });
});

describe("the vendor portal is addressed only by token", () => {
  it("never puts a case id in the vendor URL", async () => {
    await api.vendorForm("tok_abc123");
    expect(global.fetch.mock.calls[0][0]).toMatch(/\/api\/vendor\/onboard\/tok_abc123$/);
  });
});
