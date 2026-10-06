// Typed client for the ccmon API. All paths are relative so the app works
// behind the Issabel httpd reverse proxy (/ccmon/) as well as at "/".
export const CSRF_HEADER = "X-CCMon-CSRF";
const API = "api/v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers[CSRF_HEADER] = "1";
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    window.dispatchEvent(new Event("ccmon:unauthorized"));
    throw new ApiError(401, "session expired — please sign in again");
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    let msg: string;
    if (typeof detail === "string") msg = detail;
    else if (Array.isArray(detail))
      msg = detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ");
    else msg = JSON.stringify(detail ?? res.statusText);
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

export const get = <T>(path: string): Promise<T> => request<T>("GET", path);
export const post = <T>(path: string, body?: unknown): Promise<T> =>
  request<T>("POST", path, body);
export const patch = <T>(path: string, body: unknown): Promise<T> =>
  request<T>("PATCH", path, body);
export const del = <T>(path: string): Promise<T> => request<T>("DELETE", path);

/** Build a query string, skipping empty/undefined values. */
export function qs(params: Record<string, string | number | undefined | null>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

// ----------------------------------------------------------------- sessions
export interface Me {
  id: number;
  username: string;
  full_name: string;
  is_superadmin: boolean;
  scopes: string[];
}

// --------------------------------------------------------------------- live
export interface ListMeta {
  collected_at: string | null;
  stale: boolean;
}

export interface QueueLive {
  queue: string;
  calls_waiting: number | null;
  calls_active: number | null;
  agents_online: number | null;
  agents_available: number | null;
  agents_paused: number | null;
  agents_talking: number | null;
  handled_rolling: number | null;
  abandoned_rolling: number | null;
  avg_wait_seconds: number | null;
  sla_pct: number | null;
  source: string | null;
  ts: string | null;
}
export interface QueueList extends ListMeta {
  items: QueueLive[];
}

export interface AgentLive {
  agent: string;
  name: string | null;
  extension: string | null;
  state: string;
  pause_seconds: number | null;
  calls_handled_today: number | null;
  queue_paused: boolean | null;
  ts: string | null;
}
export interface AgentList extends ListMeta {
  items: AgentLive[];
}

export interface CampaignLive {
  campaign: string;
  status: string | null;
  agents: number | null;
  calls_today: number | null;
  connected_today: number | null;
  ts: string | null;
}
export interface CampaignList extends ListMeta {
  items: CampaignLive[];
}

export interface CallLive {
  channel: string | null;
  exten: string | null;
  caller: string | null;
  state: string | null;
  queue: string | null;
  agent: string | null;
  ts: string | null;
}
export interface CallList extends ListMeta {
  items: CallLive[];
}

export interface HealthComponent {
  component: string;
  status: string;
  detail: string | null;
  ts: string | null;
}
export interface TrunkLive {
  tech: string | null;
  peer: string;
  status: string;
  detail: string | null;
}
export interface HealthOut extends ListMeta {
  components: HealthComponent[];
  trunks: TrunkLive[];
  collector: Record<string, string | null>;
}

// ------------------------------------------------------------------ reports
export interface ReportSummary {
  date_from: string;
  date_to: string;
  offered: number;
  answered: number;
  abandoned: number;
  abandoned_pct: number;
  avg_wait_seconds: number;
  sla_pct: number;
  sla_seconds: number;
  sla_target_pct: number;
  source: string;
}
export interface SeriesPoint {
  bucket: string;
  queue: string | null;
  offered: number;
  answered: number;
  abandoned: number;
  avg_wait_seconds: number;
  sla_pct: number;
}
export interface SeriesOut {
  points: SeriesPoint[];
  truncated: boolean;
}
export interface AbandonedRow {
  ts: string;
  queue: string | null;
  wait_seconds: number | null;
  caller: string | null;
}
export interface AbandonedOut {
  date: string;
  total: number;
  by_queue: Record<string, number>;
  by_hour: Array<{ hour: string; count: number }>;
  avg_wait_seconds: number;
  rows: AbandonedRow[];
  truncated: boolean;
}
export interface AgentReportRow {
  agent: string;
  answered: number;
  abandoned: number;
  avg_wait_seconds: number;
  talk_seconds: number;
}
export interface AgentReportOut {
  rows: AgentReportRow[];
  truncated: boolean;
}
export interface CampaignReportRow {
  campaign: string;
  calls: number;
  connected: number;
  connect_rate_pct: number;
}
export interface CampaignReportOut {
  rows: CampaignReportRow[];
  truncated: boolean;
}

// -------------------------------------------------------------------- alert
export type RuleType =
  | "queue_waiting_gt"
  | "queue_abandoned_gt"
  | "agent_paused_minutes_gt"
  | "no_agents_available"
  | "trunk_not_registered"
  | "service_down";
export type Severity = "info" | "warning" | "critical";

export interface AlertRule {
  id: number;
  type: RuleType;
  name: string;
  params: Record<string, unknown>;
  severity: Severity;
  for_seconds: number;
  cooldown_minutes: number;
  enabled: boolean;
  created_at: string;
  updated_at: string | null;
  last_state: string | null;
  last_fired_at: string | null;
  last_resolved_at: string | null;
}
export interface AlertEvent {
  id: number;
  rule_id: number;
  ts: string;
  state: string;
  message: string;
  payload: Record<string, unknown> | null;
}

// ------------------------------------------------------------------- meta
export interface Capabilities {
  mode: string;
  databases: Record<string, boolean>;
  ami: boolean;
  systemd: boolean;
  telegram: boolean;
  openapi: boolean;
}

// ------------------------------------------------------------------ admin
export interface TokenOut {
  id: number;
  name: string;
  scopes: string[];
  created_at: string;
  expires_at: string | null;
  revoked: boolean;
  token?: string;
}
export interface UserOut {
  id: number;
  username: string;
  full_name: string;
  is_superadmin: boolean;
}
export interface AuditRow {
  ts: string | null;
  username: string | null;
  action: string;
  detail: string | null;
  ip: string | null;
}

export const paths = {
  live: {
    queues: `${API}/live/queues`,
    agents: `${API}/live/agents`,
    campaigns: `${API}/live/campaigns`,
    calls: `${API}/live/calls`,
    health: `${API}/live/health`,
  },
  auth: {
    login: `${API}/auth/login`,
    logout: `${API}/auth/logout`,
    me: `${API}/auth/me`,
  },
  meta: {
    capabilities: `${API}/meta/capabilities`,
  },
  reports: `${API}/reports`,
  alertRules: `${API}/alert-rules`,
  alertEvents: `${API}/alerts/events`,
  admin: {
    tokens: `${API}/admin/tokens`,
    users: `${API}/admin/users`,
    audit: `${API}/admin/audit`,
  },
};
