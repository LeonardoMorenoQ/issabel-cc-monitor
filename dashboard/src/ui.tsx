import { useEffect, useState, type ReactNode } from "react";
import { ApiError, get } from "./api";

// --------------------------------------------------------------- formatting
export const n = (v: number | null | undefined): number => v ?? 0;

export function fmtInt(v: number | null | undefined): string {
  return n(v).toLocaleString();
}

export function fmtPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(1)}%`;
}

export function fmtSec(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v >= 90 ? `${Math.round(v / 60)}m` : `${Math.round(v)}s`;
}

export function fmtDur(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(n(seconds)));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 90) return `${Math.round(secs)}s ago`;
  if (secs < 5400) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

// ------------------------------------------------------------------- badges
export function Badge(props: { value: string | null | undefined; tone?: string }) {
  const v = props.value ?? "—";
  let tone = props.tone;
  if (!tone) {
    const low = v.toLowerCase();
    if (["ok", "up", "registered", "online", "active", "resolved", "healthy", "green"].includes(low))
      tone = "ok";
    else if (["warning", "paused", "degraded", "pending", "yellow"].includes(low)) tone = "warn";
    else if (["down", "bad", "critical", "firing", "unregistered", "offline", "error", "red"].includes(low))
      tone = "bad";
    else tone = "info";
  }
  return <span className={`badge ${tone}`}>{v}</span>;
}

// ------------------------------------------------------------------ polling
export interface PollResult<T> {
  data: T | null;
  error: string | null;
}

/** Poll a JSON endpoint every `intervalMs`; surfaces errors for banners. */
export function usePoll<T>(path: string | null, intervalMs: number): PollResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const tick = async () => {
      try {
        const d = await get<T>(path);
        if (alive) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.message : String(e));
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), intervalMs);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [path, intervalMs]);
  return { data, error };
}

// ------------------------------------------------------------------ widgets
export function StaleBanner(props: { stale?: boolean; collectedAt?: string | null }) {
  if (!props.stale) return null;
  return (
    <div className="banner warn">
      Data is stale{props.collectedAt ? ` — last update ${fmtTs(props.collectedAt)}` : ""}
      . Check that <code>ccmon collect</code> is running.
    </div>
  );
}

export function ErrorBanner({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="banner bad">{error}</div>;
}

export function Card(props: { label: string; value: string; hint?: string }) {
  return (
    <div className="card">
      <div className="label">{props.label}</div>
      <div className="value">{props.value}</div>
      {props.hint ? <div className="hint">{props.hint}</div> : null}
    </div>
  );
}

export function Table(props: {
  head: Array<[string, boolean?]>;
  rows: ReactNode[][];
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {props.head.map(([label, num], i) => (
              <th key={i} className={num ? "num" : undefined}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {props.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className={props.head[j][1] ? "num" : undefined}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
