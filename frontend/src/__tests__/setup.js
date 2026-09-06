import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import useAppStore from "../store";

// The zustand store is a module singleton, so without this one test's cached
// dashboard would render inside the next one.
const PRISTINE = useAppStore.getState();

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAppStore.setState({ ...PRISTINE, signedIn: false, ready: false, cache: {} },
                        true);
});

// jsdom implements neither of these, and several pages use them.
window.confirm = () => true;
if (!navigator.clipboard) {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: () => Promise.resolve() },
    configurable: true,
  });
}
