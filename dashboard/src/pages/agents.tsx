import { paths, type AgentList } from "../api";
import {
  Badge,
  ErrorBanner,
  StaleBanner,
  Table,
  fmtDur,
  fmtInt,
  fmtTs,
  usePoll,
} from "../ui";

export function AgentsPage() {
  const { data, error } = usePoll<AgentList>(paths.live.agents, 5000);
  return (
    <>
      <h1>Agents</h1>
      <p className="sub">Presence, pauses and today's handled calls.</p>
      <ErrorBanner error={error} />
      <StaleBanner stale={data?.stale} collectedAt={data?.collected_at} />
      <div className="panel table-wrap">
        <Table
          head={[
            ["Agent"],
            ["Name"],
            ["Extension"],
            ["State"],
            ["On pause", true],
            ["Handled today", true],
            ["Queue-paused"],
            ["Updated"],
          ]}
          rows={(data?.items ?? []).map((a) => [
            <code key={a.agent}>{a.agent}</code>,
            a.name ?? "—",
            a.extension ? <code key={a.extension}>{a.extension}</code> : "—",
            <Badge key={a.agent} value={a.state} />,
            a.pause_seconds ? fmtDur(a.pause_seconds) : "—",
            fmtInt(a.calls_handled_today),
            <Badge key={`p${a.agent}`} value={a.queue_paused ? "paused" : "active"} />,
            fmtTs(a.ts),
          ])}
        />
        {(data?.items ?? []).length === 0 ? (
          <p className="muted">No agent snapshots yet.</p>
        ) : null}
      </div>
    </>
  );
}
