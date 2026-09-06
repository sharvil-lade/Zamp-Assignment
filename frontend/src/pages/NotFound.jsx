import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <main className="narrow" style={{ paddingTop: 80 }}>
      <h1>We couldn&rsquo;t find that.</h1>
      <p className="faint">
        The page you asked for does not exist, or you followed a link that has
        since changed.
      </p>
      <Link className="btn primary" to="/dashboard">
        Back to the dashboard
      </Link>
    </main>
  );
}
