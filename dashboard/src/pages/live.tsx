import { paths, type AgentList, type CallList, type QueueList } from "../api";
import {
  Badge,
  Card,
  ErrorBanner,
  StaleBanner,
  Table,
  fmtDur,
  fmtInt,
  fmtPct,
  fmtSec,
  fmtTs,
  n,
  usePoll,
} from "../ui";

const LIVE_MS = 5000;

export function LivePage() {
  const queues = usePoll<QueueList>(paths.live.queues, LIVE_MS);
  const agents = usePoll<AgentList>(paths.live.agents, LIVE_MS);
  const calls = usePoll<CallList>(paths.live.calls, LIVE_MS);

  const ql = queues.data;
  const al = agents.data;
  const cl = calls.data;
  const error = queues.error ?? agents.error ?? calls.error;

  const waiting = ql?.items.reduce((s, q) => s + n(q.calls_waiting), 0) ?? 0;
  const active = ql?.items.reduce((s, q) => s + n(q.calls_active), 0) ?? 0;
  const byState = new Map<string, number>();
  for (const a of al?.items ?? [])
    byState.set(a.state, (byState.get(a.state) ?? 0) + 1);

  return (
    <>
      <h1>Live</h1>
      <p className="sub">Refreshes every 5 seconds.</p>
      <ErrorBanner error={error} />
      <StaleBanner stale={ql?.stale} collectedAt={ql?.collected_at} />
      <div className="grid" style={{ marginBottom: 14 }}>
        <Card label="Calls waiting" value={fmtInt(waiting)} />
        <Card label="Calls active" value={fmtInt(active)} />
        <Card
          label="Agents"
          value={fmtInt(al?.items.length)}
          hint={[...byState.entries()].map(([k, v]) => `${k}: ${v}`).join(" · ")}
        />
        <Card
          label="Active channels"
          value={fmtInt(cl?.items.length)}
          hint={`collected ${fmtTs(ql?.collected_at)}`}
        />
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Queues</h2>
        <Table
          head={[
            ["Queue"],
            ["Waiting", true],
            ["Active", true],
            ["Avail", true],
            ["Paused", true],
            ["SLA", true],
            ["Avg wait", true],
            ["Handled 15m", true],
            ["Aband. 15m", true],
          ]}
          rows={(ql?.items ?? []).map((q) => [
            <code key={q.queue}>{q.queue}</code>,
            fmtInt(q.calls_waiting),
            fmtInt(q.calls_active),
            fmtInt(q.agents_available),
            fmtInt(q.agents_paused),
            fmtPct(q.sla_pct),
            fmtSec(q.avg_wait_seconds),
            fmtInt(q.handled_rolling),
            fmtInt(q.abandoned_rolling),
          ])}
        />
        {(ql?.items ?? []).length === 0 ? (
          <p className="muted">No queue snapshots yet.</p>
        ) : null}
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Agents</h2>
        <Table
          head={[
            ["Agent"],
            ["Name"],
            ["State"],
            ["Paused for", true],
            ["Handled today", true],
          ]}
          rows={(al?.items ?? []).map((a) => [
            <code key={a.agent}>{a.agent}</code>,
            a.name ?? "—",
            <Badge key={a.agent} value={a.state} />,
            a.state === "paused" ? fmtDur(a.pause_seconds) : "—",
            fmtInt(a.calls_handled_today),
          ])}
        />
        {(al?.items ?? []).length === 0 ? (
          <p className="muted">No agent snapshots yet.</p>
        ) : null}
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Active calls</h2>
        <Table
          head={[["Channel"], ["Caller"], ["Queue"], ["Agent"], ["State"]]}
          rows={(cl?.items ?? []).map((c, i) => [
            <code key={i}>{c.channel ?? "—"}</code>,
            c.caller ?? "—",
            c.queue ? <code key={`q${i}`}>{c.queue}</code> : "—",
            c.agent || "—",
            <Badge key={i} value={c.state} />,
          ])}
        />
        {(cl?.items ?? []).length === 0 ? (
          <p className="muted">No active calls.</p>
        ) : null}
      </div>
    </>
  );
}
