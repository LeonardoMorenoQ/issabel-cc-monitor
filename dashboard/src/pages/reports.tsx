import { useEffect, useState } from "react";
import {
  get,
  paths,
  qs,
  type AbandonedOut,
  type AgentReportOut,
  type CampaignReportOut,
  type QueueList,
  type ReportSummary,
  type SeriesOut,
} from "../api";
import {
  ErrorBanner,
  Table,
  fmtInt,
  fmtPct,
  fmtSec,
  fmtTs,
} from "../ui";

const R = paths.reports;

function isoDay(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

export function ReportsPage() {
  const [dateFrom, setDateFrom] = useState(isoDay(-7));
  const [dateTo, setDateTo] = useState(isoDay(0));
  const [bucket, setBucket] = useState<"hour" | "day">("day");
  const [queue, setQueue] = useState("");
  const [queues, setQueues] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [series, setSeries] = useState<SeriesOut | null>(null);
  const [agents, setAgents] = useState<AgentReportOut | null>(null);
  const [campaigns, setCampaigns] = useState<CampaignReportOut | null>(null);
  const [abandoned, setAbandoned] = useState<AbandonedOut | null>(null);

  useEffect(() => {
    get<QueueList>(paths.live.queues)
      .then((d) => setQueues(d.items.map((q) => q.queue)))
      .catch(() => setQueues([]));
  }, []);

  const run = async () => {
    setBusy(true);
    setError(null);
    const range = { date_from: dateFrom, date_to: dateTo };
    try {
      const [s, se, ag, ab, ca] = await Promise.all([
        get<ReportSummary>(`${R}/summary${qs(range)}`),
        get<SeriesOut>(`${R}/series${qs({ ...range, bucket, queue })}`),
        get<AgentReportOut>(`${R}/agents${qs(range)}`),
        get<AbandonedOut>(`${R}/abandoned${qs({ date: dateTo, queue })}`),
        get<CampaignReportOut>(`${R}/campaigns${qs(range)}`),
      ]);
      setSummary(s);
      setSeries(se);
      setAgents(ag);
      setAbandoned(ab);
      setCampaigns(ca);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const maxOffered = Math.max(1, ...(series?.points ?? []).map((p) => p.offered));

  return (
    <>
      <h1>Reports</h1>
      <p className="sub">
        Historical data from asterisk.queue_log / CDR. Ranges are capped at 92 days.
      </p>
      <div className="panel">
        <div className="row">
          <label className="field">
            <span>From</span>
            <input
              type="date"
              value={dateFrom}
              max={dateTo}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </label>
          <label className="field">
            <span>To</span>
            <input
              type="date"
              value={dateTo}
              min={dateFrom}
              max={isoDay(0)}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Bucket</span>
            <select
              value={bucket}
              onChange={(e) => setBucket(e.target.value as "hour" | "day")}
            >
              <option value="day">day</option>
              <option value="hour">hour</option>
            </select>
          </label>
          <label className="field">
            <span>Queue</span>
            <select value={queue} onChange={(e) => setQueue(e.target.value)}>
              <option value="">all queues</option>
              {queues.map((q) => (
                <option key={q} value={q}>
                  {q}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => void run()} disabled={busy}>
            {busy ? "Running…" : "Run report"}
          </button>
        </div>
      </div>
      <ErrorBanner error={error} />

      {summary ? (
        <>
          <div className="grid" style={{ marginBottom: 14 }}>
            <Stat label="Offered" value={fmtInt(summary.offered)} />
            <Stat label="Answered" value={fmtInt(summary.answered)} />
            <Stat
              label="Abandoned"
              value={fmtInt(summary.abandoned)}
              hint={fmtPct(summary.abandoned_pct)}
            />
            <Stat
              label="SLA"
              value={fmtPct(summary.sla_pct)}
              hint={`≤ ${summary.sla_seconds}s target ${fmtPct(summary.sla_target_pct)}`}
            />
            <Stat label="Avg wait" value={fmtSec(summary.avg_wait_seconds)} />
          </div>

          <div className="panel">
            <h2 style={{ marginTop: 0 }}>
              Trend ({bucket === "hour" ? "hourly" : "daily"})
            </h2>
            <Table
              head={[
                ["Bucket"],
                ["Offered"],
                ["Answered"],
                ["Abandoned"],
                ["Avg wait", true],
                ["SLA", true],
              ]}
              rows={(series?.points ?? []).map((p) => [
                <code key={p.bucket}>{p.bucket}</code>,
                <span className="bar-cell" key={`b${p.bucket}`}>
                  <span
                    className="bar"
                    style={{ width: `${(40 * p.offered) / maxOffered}px` }}
                  />
                  {fmtInt(p.offered)}
                </span>,
                fmtInt(p.answered),
                fmtInt(p.abandoned),
                fmtSec(p.avg_wait_seconds),
                fmtPct(p.sla_pct),
              ])}
            />
            {(series?.points ?? []).length === 0 ? (
              <p className="muted">No data in this range.</p>
            ) : null}
            {series?.truncated ? (
              <p className="muted">Result truncated — narrow the date range.</p>
            ) : null}
          </div>
        </>
      ) : null}

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Agents</h2>
        <Table
          head={[
            ["Agent"],
            ["Answered", true],
            ["Abandoned", true],
            ["Avg wait", true],
            ["Talk time", true],
          ]}
          rows={(agents?.rows ?? []).map((r) => [
            <code key={r.agent}>{r.agent}</code>,
            fmtInt(r.answered),
            fmtInt(r.abandoned),
            fmtSec(r.avg_wait_seconds),
            fmtSec(r.talk_seconds),
          ])}
        />
        {(agents?.rows ?? []).length === 0 ? (
          <p className="muted">No agent activity in this range.</p>
        ) : null}
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Campaigns</h2>
        <Table
          head={[
            ["Campaign"],
            ["Calls", true],
            ["Connected", true],
            ["Connect rate", true],
          ]}
          rows={(campaigns?.rows ?? []).map((r) => [
            <code key={r.campaign}>{r.campaign}</code>,
            fmtInt(r.calls),
            fmtInt(r.connected),
            fmtPct(r.connect_rate_pct),
          ])}
        />
        {(campaigns?.rows ?? []).length === 0 ? (
          <p className="muted">No campaign activity in this range.</p>
        ) : null}
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>
          Abandoned calls — {abandoned?.date ?? dateTo}
        </h2>
        {abandoned ? (
          <>
            <p className="muted">
              {fmtInt(abandoned.total)} abandoned · avg wait{" "}
              {fmtSec(abandoned.avg_wait_seconds)}
            </p>
            <Table
              head={[["Time"], ["Queue"], ["Wait", true], ["Caller"]]}
              rows={(abandoned.rows ?? []).map((r, i) => [
                <span key={i}>{fmtTs(r.ts)}</span>,
                r.queue ? <code key={`q${i}`}>{r.queue}</code> : "—",
                fmtSec(r.wait_seconds),
                r.caller ?? "—",
              ])}
            />
            {(abandoned.rows ?? []).length === 0 ? (
              <p className="muted">No abandoned calls on this day.</p>
            ) : null}
            {abandoned.truncated ? (
              <p className="muted">Truncated — showing the first rows only.</p>
            ) : null}
          </>
        ) : (
          <p className="muted">Run a report to see abandoned calls.</p>
        )}
      </div>
    </>
  );
}

function Stat(props: { label: string; value: string; hint?: string }) {
  return (
    <div className="card">
      <div className="label">{props.label}</div>
      <div className="value">{props.value}</div>
      {props.hint ? <div className="hint">{props.hint}</div> : null}
    </div>
  );
}
