import { Fragment, useState } from "react";

/** The small pieces every page needs. Presentation only — no fetching. */

/**
 * Light/dark, on the one attribute the stylesheet keys off. The initial value
 * is set by an inline script in index.html before paint, so this reads it back
 * rather than deciding it — otherwise every reload flashes the wrong theme.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme || "light"
  );
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      className="theme"
      type="button"
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
      onClick={() => {
        document.documentElement.dataset.theme = next;
        localStorage.setItem("theme", next);
        setTheme(next);
      }}
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}

export function Badge({ status, label, className = "" }) {
  const tone = (status || "").toLowerCase();
  const known = ["approved", "pending", "rejected", "error"].includes(tone);
  return (
    <span className={`badge ${known ? tone : "neutral"} ${className}`.trim()}>
      {label || status}
    </span>
  );
}

export function Loading({ what = "Loading" }) {
  return (
    <p className="muted" role="status">
      <span className="spin" /> {what}…
    </p>
  );
}

/** Every failed request looks the same, so no page has to invent an error UI. */
export function ErrorBox({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="alert bad" role="alert">
      {error.detail || error.message || "Something went wrong."}
      {onRetry && (
        <>
          {" "}
          <button className="link" onClick={onRetry}>
            Try again
          </button>
        </>
      )}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

/** A definition list. Used wherever the UI shows "field: value" pairs. */
export function KeyValues({ items }) {
  return (
    <dl className="kv">
      {items.map(([key, value]) => (
        <Fragment key={key}>
          <dt>{key}</dt>
          <dd>{value ?? "—"}</dd>
        </Fragment>
      ))}
    </dl>
  );
}
