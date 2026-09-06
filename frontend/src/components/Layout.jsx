import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import useSession from "../hooks/useSession";
import { Loading, ThemeToggle } from "./ui";

const NAV = [
  ["/dashboard", "Dashboard"],
  ["/forms", "Forms"],
];

/**
 * The employee shell. Everything inside it requires a session; anything that
 * does not (the vendor portal, the login page) is routed outside it.
 */
export default function Layout() {
  const { signedIn, ready, signOut } = useSession();
  const location = useLocation();

  if (!ready) return <main><Loading what="Checking your session" /></main>;
  if (!signedIn) {
    // Remember where they were headed so login can send them back.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return (
    <>
      <header className="header">
        <div className="header-inner">
          <NavLink to="/dashboard" className="brand">
            VendorFlow
          </NavLink>
          <nav className="nav">
            {NAV.map(([to, label]) => (
              <NavLink key={to} to={to} className={({ isActive }) => (isActive ? "active" : "")}>
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="who">
            <ThemeToggle />
            <button onClick={signOut}>Sign out</button>
          </div>
        </div>
      </header>
      <main>
        <Outlet />
      </main>
    </>
  );
}
