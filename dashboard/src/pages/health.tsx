import { paths, type HealthOut } from "../api";
import {
  Badge,
  ErrorBanner,
  StaleBanner,
  Table,
  fmtTs,
  usePoll,
} from "../ui";

export function HealthPage() {
  const { data, error } = usePoll<HealthOut>(paths.live.health, 5000);
  return (
    <>
      <h1>Health</h1>
      <p className="sub">Services, SIP trunks and collector heartbeats.</p>
      <ErrorBanner error={error} />
      <StaleBanner stale={data?.stale} collectedAt={data?.collected_at} />

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Services</h2>
        <Table
          head={[["Component"], ["Status"], ["Detail"], ["Updated"]]}
          rows={(data?.components ?? []).map((c) => [
            <code key={c.component}>{c.component}</code>,
            <Badge key={c.component} value={c.status} />,
            c.detail ?? "—",
            fmtTs(c.ts),
          ])}
        />
        {(data?.components ?? []).length === 0 ? (
          <p className="muted">No service probes recorded yet.</p>
        ) : null}
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Trunks</h2>
        <Table
          head={[["Tech"], ["Peer"], ["Status"], ["Detail"]]}
          rows={(data?.trunks ?? []).map((t) => [
            t.tech ?? "—",
            <code key={t.peer}>{t.peer}</code>,
            <Badge key={t.peer} value={t.status} />,
            t.detail ?? "—",
          ])}
        />
        {(data?.trunks ?? []).length === 0 ? (
          <p className="muted">No trunk state recorded yet.</p>
        ) : null}
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Collector heartbeats</h2>
        <Table
          head={[["Loop"], ["Last beat"]]}
          rows={Object.entries(data?.collector ?? {}).map(([key, ts]) => [
            <code key={key}>{key}</code>,
            fmtTs(ts),
          ])}
        />
        {Object.keys(data?.collector ?? {}).length === 0 ? (
          <p className="muted">No heartbeats — is the collector running?</p>
        ) : null}
      </div>
    </>
  );
}
