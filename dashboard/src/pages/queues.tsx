import { paths, type QueueList } from "../api";
import {
  ErrorBanner,
  StaleBanner,
  Table,
  fmtInt,
  fmtPct,
  fmtSec,
  fmtTs,
  usePoll,
} from "../ui";

export function QueuesPage() {
  const { data, error } = usePoll<QueueList>(paths.live.queues, 5000);
  return (
    <>
      <h1>Queues</h1>
      <p className="sub">Current state and 15-minute rolling counters.</p>
      <ErrorBanner error={error} />
      <StaleBanner stale={data?.stale} collectedAt={data?.collected_at} />
      <div className="panel table-wrap">
        <Table
          head={[
            ["Queue"],
            ["Waiting", true],
            ["Active", true],
            ["Online", true],
            ["Available", true],
            ["Paused", true],
            ["Talking", true],
            ["Handled 15m", true],
            ["Aband. 15m", true],
            ["Avg wait", true],
            ["SLA", true],
            ["Source"],
            ["Updated"],
          ]}
          rows={(data?.items ?? []).map((q) => [
            <code key={q.queue}>{q.queue}</code>,
            fmtInt(q.calls_waiting),
            fmtInt(q.calls_active),
            fmtInt(q.agents_online),
            fmtInt(q.agents_available),
            fmtInt(q.agents_paused),
            fmtInt(q.agents_talking),
            fmtInt(q.handled_rolling),
            fmtInt(q.abandoned_rolling),
            fmtSec(q.avg_wait_seconds),
            fmtPct(q.sla_pct),
            q.source ?? "—",
            fmtTs(q.ts),
          ])}
        />
        {(data?.items ?? []).length === 0 ? (
          <p className="muted">No queue snapshots yet.</p>
        ) : null}
      </div>
    </>
  );
}
