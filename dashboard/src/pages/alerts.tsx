import { useCallback, useEffect, useState } from "react";
import {
  del,
  get,
  patch,
  paths,
  post,
  type AlertEvent,
  type AlertRule,
  type RuleType,
  type Severity,
} from "../api";
import { Badge, ErrorBanner, Table, ago, fmtTs } from "../ui";

interface ParamField {
  k: string;
  label: string;
  type: "string" | "number";
  required?: boolean;
  def?: string | number;
}

const RULE_TYPES: Record<RuleType, { label: string; fields: ParamField[] }> = {
  queue_waiting_gt: {
    label: "Queue waiting > N",
    fields: [
      { k: "queue", label: "Queue", type: "string", required: true },
      { k: "threshold", label: "Waiting above", type: "number", required: true },
    ],
  },
  queue_abandoned_gt: {
    label: "Abandoned calls > N in window",
    fields: [
      { k: "queue", label: "Queue (blank = all)", type: "string" },
      { k: "threshold", label: "Abandoned above", type: "number", required: true },
      { k: "window_minutes", label: "Window minutes", type: "number", def: 15 },
    ],
  },
  agent_paused_minutes_gt: {
    label: "Agent paused too long",
    fields: [
      { k: "min_minutes", label: "Minutes", type: "number", def: 15 },
      { k: "agent", label: "Agent (blank = any)", type: "string" },
    ],
  },
  no_agents_available: {
    label: "No agents available",
    fields: [{ k: "queue", label: "Queue (blank = any)", type: "string" }],
  },
  trunk_not_registered: {
    label: "Trunk not registered",
    fields: [{ k: "peer", label: "Peer (blank = any)", type: "string" }],
  },
  service_down: {
    label: "Systemd service down",
    fields: [
      { k: "component", label: "Component (e.g. asterisk)", type: "string", required: true },
    ],
  },
};

export function AlertsPage() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [r, e] = await Promise.all([
        get<AlertRule[]>(paths.alertRules),
        get<AlertEvent[]>(`${paths.alertEvents}?limit=50`),
      ]);
      setRules(r);
      setEvents(e);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 10000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setError(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <h1>Alerts</h1>
      <p className="sub">
        Rules evaluate every collection cycle; alerts fire after the sustain window and
        notify Telegram when configured.
      </p>
      <ErrorBanner error={error} />

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Rules</h2>
        <Table
          head={[
            ["Name"],
            ["Type"],
            ["Severity"],
            ["Params"],
            ["Sustain", true],
            ["Cooldown", true],
            ["State"],
            ["Fired"],
            [""],
          ]}
          rows={rules.map((r) => [
            <strong key={r.id}>{r.name}</strong>,
            <code key={`t${r.id}`}>{r.type}</code>,
            <Badge key={`s${r.id}`} value={r.severity} tone={r.severity === "critical" ? "bad" : r.severity === "warning" ? "warn" : "info"} />,
            <code key={`p${r.id}`}>{JSON.stringify(r.params)}</code>,
            `${r.for_seconds}s`,
            `${r.cooldown_minutes}m`,
            r.enabled ? (
              <Badge key={`e${r.id}`} value={r.last_state ?? "idle"} />
            ) : (
              <Badge key={`e${r.id}`} value="disabled" tone="info" />
            ),
            ago(r.last_fired_at),
            <span key={`b${r.id}`}>
              <button
                className="secondary small"
                onClick={() =>
                  void act(() => patch(paths.alertRules + `/${r.id}`, { enabled: !r.enabled }))
                }
              >
                {r.enabled ? "Disable" : "Enable"}
              </button>{" "}
              <button
                className="danger small"
                onClick={() => {
                  if (window.confirm(`Delete rule “${r.name}”?`))
                    void act(() => del(paths.alertRules + `/${r.id}`));
                }}
              >
                Delete
              </button>
            </span>,
          ])}
        />
        {rules.length === 0 ? <p className="muted">No rules configured.</p> : null}
      </div>

      <NewRule onCreated={() => void refresh()} onError={setError} />

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Recent events</h2>
        <Table
          head={[["When"], ["State"], ["Message"]]}
          rows={events.map((e) => [
            <span key={e.id}>{fmtTs(e.ts)}</span>,
            <Badge key={`s${e.id}`} value={e.state} />,
            <span key={`m${e.id}`}>{e.message}</span>,
          ])}
        />
        {events.length === 0 ? (
          <p className="muted">No alert events yet.</p>
        ) : null}
      </div>
    </>
  );
}

function NewRule(props: { onCreated: () => void; onError: (msg: string) => void }) {
  const [type, setType] = useState<RuleType>("queue_waiting_gt");
  const [name, setName] = useState("");
  const [severity, setSeverity] = useState<Severity>("warning");
  const [forSeconds, setForSeconds] = useState(60);
  const [cooldown, setCooldown] = useState(10);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const fields = RULE_TYPES[type].fields;

  const changeType = (t: RuleType) => {
    setType(t);
    const init: Record<string, string> = {};
    for (const f of RULE_TYPES[t].fields)
      if (f.def !== undefined) init[f.k] = String(f.def);
    setValues(init);
    setError(null);
  };

  const submit = async () => {
    const params: Record<string, unknown> = {};
    for (const f of fields) {
      const raw = (values[f.k] ?? "").trim();
      if (raw === "") {
        if (f.required) {
          setError(`${f.label} is required`);
          return;
        }
        continue;
      }
      params[f.k] = f.type === "number" ? Number(raw) : raw;
    }
    setBusy(true);
    setError(null);
    try {
      await post(paths.alertRules, {
        type,
        name: name.trim() || RULE_TYPES[type].label,
        params,
        severity,
        for_seconds: forSeconds,
        cooldown_minutes: cooldown,
        enabled: true,
      });
      setName("");
      setValues({});
      props.onCreated();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      props.onError(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>New rule</h2>
      <div className="row">
        <label className="field">
          <span>Type</span>
          <select
            value={type}
            onChange={(e) => changeType(e.target.value as RuleType)}
          >
            {Object.entries(RULE_TYPES).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Name</span>
          <input
            value={name}
            placeholder={RULE_TYPES[type].label}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Severity</span>
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as Severity)}
          >
            <option value="info">info</option>
            <option value="warning">warning</option>
            <option value="critical">critical</option>
          </select>
        </label>
        <label className="field">
          <span>Sustain (s)</span>
          <input
            type="number"
            min={0}
            value={forSeconds}
            onChange={(e) => setForSeconds(Number(e.target.value))}
          />
        </label>
        <label className="field">
          <span>Cooldown (min)</span>
          <input
            type="number"
            min={0}
            value={cooldown}
            onChange={(e) => setCooldown(Number(e.target.value))}
          />
        </label>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        {fields.map((f) => (
          <label className="field" key={f.k}>
            <span>{f.label}</span>
            <input
              type={f.type === "number" ? "number" : "text"}
              value={values[f.k] ?? ""}
              onChange={(e) => setValues({ ...values, [f.k]: e.target.value })}
            />
          </label>
        ))}
        <button onClick={() => void submit()} disabled={busy}>
          {busy ? "Saving…" : "Create rule"}
        </button>
      </div>
      {error ? <div className="error-text">{error}</div> : null}
    </div>
  );
}
