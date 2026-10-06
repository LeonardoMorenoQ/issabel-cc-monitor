import { useCallback, useEffect, useState } from "react";
import { get, paths, post, type Me } from "./api";
import { Login } from "./pages/login";
import { LivePage } from "./pages/live";
import { QueuesPage } from "./pages/queues";
import { AgentsPage } from "./pages/agents";
import { CampaignsPage } from "./pages/campaigns";
import { HealthPage } from "./pages/health";
import { ReportsPage } from "./pages/reports";
import { AlertsPage } from "./pages/alerts";
import { SettingsPage } from "./pages/settings";

const NAV: Array<[string, string]> = [
  ["/live", "Live"],
  ["/queues", "Queues"],
  ["/agents", "Agents"],
  ["/campaigns", "Campaigns"],
  ["/health", "Health"],
  ["/reports", "Reports"],
  ["/alerts", "Alerts"],
  ["/settings", "Settings"],
];

function currentRoute(): string {
  const h = window.location.hash.replace(/^#/, "");
  return h || "/live";
}

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);
  const [route, setRoute] = useState(currentRoute);

  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    const on401 = () => setMe(null);
    window.addEventListener("ccmon:unauthorized", on401);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("ccmon:unauthorized", on401);
    };
  }, []);

  useEffect(() => {
    get<Me>(paths.auth.me)
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setChecking(false));
  }, []);

  const logout = useCallback(async () => {
    try {
      await post(paths.auth.logout);
    } finally {
      setMe(null);
    }
  }, []);

  if (checking) return <div className="center">loading…</div>;
  if (!me) return <Login onLogin={setMe} />;

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">ccmon</div>
        {NAV.map(([href, label]) => (
          <a
            key={href}
            className={`nav-link${route === href ? " active" : ""}`}
            href={`#${href}`}
          >
            {label}
          </a>
        ))}
        <div className="spacer" />
        <div className="whoami">
          {me.full_name || me.username}
          {me.is_superadmin ? " · admin" : ""}
          <div style={{ marginTop: 6 }}>
            <button className="secondary small" onClick={() => void logout()}>
              Sign out
            </button>
          </div>
        </div>
      </nav>
      <main className="main">
        {route === "/queues" ? <QueuesPage /> : null}
        {route === "/agents" ? <AgentsPage /> : null}
        {route === "/campaigns" ? <CampaignsPage /> : null}
        {route === "/health" ? <HealthPage /> : null}
        {route === "/reports" ? <ReportsPage /> : null}
        {route === "/alerts" ? <AlertsPage /> : null}
        {route === "/settings" ? <SettingsPage me={me} /> : null}
        {route !== "/queues" &&
        route !== "/agents" &&
        route !== "/campaigns" &&
        route !== "/health" &&
        route !== "/reports" &&
        route !== "/alerts" &&
        route !== "/settings" ? (
          <LivePage />
        ) : null}
      </main>
    </div>
  );
}
