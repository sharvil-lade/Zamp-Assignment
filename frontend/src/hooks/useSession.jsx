import { useEffect } from "react";
import api from "../api";
import useAppStore from "../store";

/**
 * Whether this browser is signed in.
 *
 * One shared password, so there is nothing to know about *who* — only whether.
 * The answer lives in the zustand store rather than a context, so anything can
 * read it without being wrapped, and so signing out can clear the cached server
 * state in the same action.
 */
export function SessionProvider({ children }) {
  const setSession = useAppStore((s) => s.setSession);

  // Asked once on boot: a stored token may have expired while the tab was
  // closed, and only the server can say. `/api/session` answering
  // `authenticated: false` covers both no token and no longer a valid one.
  useEffect(() => {
    api.session()
      .then((s) => setSession(Boolean(s.authenticated)))
      .catch(() => setSession(false));
  }, [setSession]);

  return children;
}

export function useSession() {
  const signedIn = useAppStore((s) => s.signedIn);
  const ready = useAppStore((s) => s.ready);
  const setSession = useAppStore((s) => s.setSession);
  const clearCache = useAppStore((s) => s.clearCache);

  return {
    signedIn,
    ready,
    signIn: async (password) => {
      await api.login(password);
      // A previous session's dashboard must not flash up under a new one.
      clearCache();
      setSession(true);
    },
    signOut: () => {
      api.logout();
      clearCache();
      setSession(false);
    },
  };
}

export default useSession;
