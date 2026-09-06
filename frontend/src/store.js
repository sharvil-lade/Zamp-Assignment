import { create } from "zustand";

/**
 * The app's shared state: who is signed in, and what the server last told us.
 *
 * The cache is the point. Every page used to refetch from scratch on mount, so
 * navigating back to the dashboard showed a spinner and then the same rows
 * again. Cached entries render immediately and are revalidated in the
 * background — stale-while-revalidate, in about twenty lines and no data layer.
 *
 * Deliberately not persisted. This holds vendor bank details and decisions;
 * writing that to localStorage so it survives a browser restart is a liability,
 * not a feature. Signing out clears it outright.
 */
export const useAppStore = create((set, get) => ({
  // --- session ------------------------------------------------------------
  // One shared password, so there is nothing to know about *who* — only whether.
  signedIn: false,
  ready: false,
  setSession: (signedIn) => set({ signedIn, ready: true }),

  // --- server state -------------------------------------------------------
  cache: {},

  cached: (key) => get().cache[key],

  put: (key, data) =>
    set((state) => ({ cache: { ...state.cache, [key]: data } })),

  /** Drop everything under a prefix, e.g. `forms` after deleting a template. */
  invalidate: (prefix) =>
    set((state) => ({
      cache: Object.fromEntries(
        Object.entries(state.cache).filter(([key]) => !key.startsWith(prefix))),
    })),

  /** On sign-out. The next person at this browser must not see the last one's
   *  dashboard while their own request is still in flight. */
  clearCache: () => set({ cache: {} }),
}));

export default useAppStore;
