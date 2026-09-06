/**
 * Shared state.
 *
 * The symptom this fixes: every page refetched from scratch on mount, so going
 * back to the dashboard blanked it out and redrew the same rows. A cached page
 * must render immediately and revalidate quietly behind it — and must never
 * outlive the session it was fetched in.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import useResource from "../hooks/useResource";
import useAppStore from "../store";

const PRISTINE = useAppStore.getState();

beforeEach(() => {
  useAppStore.setState({ ...PRISTINE, signedIn: false, ready: false, cache: {} },
                       true);
});

/** A page that shows whatever the loader returned, and says when it is loading. */
function Page({ loader, cacheKey }) {
  const { data, loading } = useResource(loader, [], cacheKey);
  if (loading && !data) return <p>loading…</p>;
  return <p>{data?.label}</p>;
}

describe("cached resources", () => {
  it("shows a spinner the first time a page is opened", async () => {
    const loader = vi.fn(() => Promise.resolve({ label: "first" }));
    render(<Page loader={loader} cacheKey="thing" />);

    expect(screen.getByText("loading…")).toBeInTheDocument();
    expect(await screen.findByText("first")).toBeInTheDocument();
  });

  it("renders instantly on the way back, with no loading state at all", async () => {
    const loader = vi.fn(() => Promise.resolve({ label: "first" }));
    const { unmount } = render(<Page loader={loader} cacheKey="thing" />);
    await screen.findByText("first");
    unmount();

    // Navigating back: the cached value is on screen in the first paint.
    render(<Page loader={loader} cacheKey="thing" />);
    expect(screen.queryByText("loading…")).not.toBeInTheDocument();
    expect(screen.getByText("first")).toBeInTheDocument();
  });

  it("still revalidates in the background, so a stale page corrects itself",
    async () => {
      let label = "first";
      const loader = vi.fn(() => Promise.resolve({ label }));
      const { unmount } = render(<Page loader={loader} cacheKey="thing" />);
      await screen.findByText("first");
      unmount();

      label = "second";                       // changed on the server meanwhile
      render(<Page loader={loader} cacheKey="thing" />);
      expect(screen.getByText("first")).toBeInTheDocument();      // instant
      expect(await screen.findByText("second")).toBeInTheDocument();  // corrected
      expect(loader).toHaveBeenCalledTimes(2);
    });

  it("keys the cache, so two pages never show each other's data", async () => {
    const one = vi.fn(() => Promise.resolve({ label: "dashboard" }));
    const two = vi.fn(() => Promise.resolve({ label: "forms" }));
    const { unmount } = render(<Page loader={one} cacheKey="dashboard" />);
    await screen.findByText("dashboard");
    unmount();

    render(<Page loader={two} cacheKey="forms" />);
    expect(screen.queryByText("dashboard")).not.toBeInTheDocument();
    expect(await screen.findByText("forms")).toBeInTheDocument();
  });

  it("caches nothing without a key, which is how the vendor portal stays cold",
    async () => {
      const loader = vi.fn(() => Promise.resolve({ label: "vendor form" }));
      const { unmount } = render(<Page loader={loader} />);
      await screen.findByText("vendor form");
      unmount();

      render(<Page loader={loader} />);
      expect(screen.getByText("loading…")).toBeInTheDocument();
      expect(useAppStore.getState().cache).toEqual({});
    });
});

describe("the cache never outlives its session", () => {
  it("is emptied on sign out", async () => {
    const loader = vi.fn(() => Promise.resolve({ label: "someone's vendors" }));
    render(<Page loader={loader} cacheKey="dashboard:all" />);
    await screen.findByText("someone's vendors");
    expect(useAppStore.getState().cache["dashboard:all"]).toBeTruthy();

    useAppStore.getState().clearCache();
    expect(useAppStore.getState().cache).toEqual({});
  });

  it("is not written to storage — it holds bank details and decisions",
    async () => {
      const loader = vi.fn(() => Promise.resolve({ label: "sensitive" }));
      render(<Page loader={loader} cacheKey="run:VS-1" />);
      await screen.findByText("sensitive");

      const stored = Object.keys(localStorage).map((k) => localStorage.getItem(k));
      expect(stored.join(" ")).not.toContain("sensitive");
    });
});

describe("invalidation", () => {
  it("drops everything under a prefix and leaves the rest alone", async () => {
    const { put, invalidate } = useAppStore.getState();
    put("forms:list", { a: 1 });
    put("forms:FORM-1", { b: 2 });
    put("dashboard:all", { c: 3 });

    invalidate("forms");
    await waitFor(() => {
      expect(Object.keys(useAppStore.getState().cache)).toEqual(["dashboard:all"]);
    });
  });
});
