import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import useSession from "../hooks/useSession";
import { Loading, ThemeToggle } from "../components/ui";

/**
 * One shared password guards the employee side. There is no sign-up, no user
 * accounts and no reset flow — this is a door, not an identity system.
 *
 * Vendors never come through here. They arrive on their own one-time link and
 * are authenticated by that token alone.
 */
export default function Login() {
  const { signedIn, ready, signIn } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const next = location.state?.from || "/dashboard";

  if (!ready)
    return <main className="signin"><Loading what="Checking your session" /></main>;
  if (signedIn) return <Navigate to={next} replace />;

  async function onSubmit(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await signIn(data.get("password"));
      navigate(next, { replace: true });
    } catch (err) {
      setError(err.detail || "That password was not recognised.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="signin">
      <ThemeToggle />

      <form className="signin-card" onSubmit={onSubmit}>
        <h1>VendorFlow</h1>
        <p className="lede">Decision Engine — enter the password to continue.</p>
        <div className="rule" />

        {error && <div className="alert bad" role="alert">{error}</div>}
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" name="password" type="password" required autoFocus
                 autoComplete="current-password" />
        </div>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
