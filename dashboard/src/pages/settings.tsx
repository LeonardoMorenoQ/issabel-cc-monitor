import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  del,
  get,
  paths,
  post,
  type AuditRow,
  type Capabilities,
  type Me,
  type TokenOut,
  type UserOut,
} from "../api";
import { Badge, ErrorBanner, Table, fmtTs } from "../ui";

export function SettingsPage({ me }: { me: Me }) {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Capabilities>(paths.meta.capabilities)
      .then(setCaps)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <>
      <h1>Settings</h1>
      <p className="sub">Instance capabilities, session and administration.</p>
      <ErrorBanner error={error} />

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Capabilities</h2>
        <Table
          head={[["Feature"], ["Value"]]}
          rows={[
            ["Mode", <code key="m">{caps?.mode ?? "…"}</code>],
            ...Object.entries(caps?.databases ?? {}).map(([k, v]) => [
              `DB: ${k}`,
              <Badge key={k} value={v ? "available" : "missing"} tone={v ? "ok" : "bad"} />,
            ]),
            ["AMI", <Badge key="ami" value={caps?.ami ? "configured" : "off"} tone={caps?.ami ? "ok" : "info"} />],
            ["systemd probes", <Badge key="sys" value={caps?.systemd ? "available" : "unavailable"} tone={caps?.systemd ? "ok" : "info"} />],
            ["Telegram", <Badge key="tg" value={caps?.telegram ? "configured" : "off"} tone={caps?.telegram ? "ok" : "info"} />],
            ["OpenAPI docs", <Badge key="oa" value={caps?.openapi ? "enabled" : "disabled"} tone={caps?.openapi ? "info" : "info"} />],
          ]}
        />
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Session</h2>
        <Table
          head={[["User"], ["Role"], ["Scopes"]]}
          rows={[
            [
              me.full_name || me.username,
              me.is_superadmin ? <Badge value="superadmin" tone="info" /> : <Badge value="user" />,
              me.scopes.join(", "),
            ],
          ]}
        />
      </div>

      {me.is_superadmin ? (
        <>
          <TokenPanel />
          <UserPanel />
          <AuditPanel />
        </>
      ) : (
        <p className="muted">Administration sections require a superadmin session.</p>
      )}
    </>
  );
}

function TokenPanel() {
  const [tokens, setTokens] = useState<TokenOut[]>([]);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    get<TokenOut[]>(paths.admin.tokens)
      .then(setTokens)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(refresh, [refresh]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const t = await post<TokenOut>(paths.admin.tokens, { name, scopes: ["read"] });
      setFresh(t.token ?? null);
      setName("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>API tokens</h2>
      <ErrorBanner error={error} />
      {fresh ? (
        <div className="banner warn">
          Copy this token now — it is shown only once: <code>{fresh}</code>
        </div>
      ) : null}
      <Table
        head={[["Name"], ["Scopes"], ["Created"], ["Expires"], ["Status"], [""]]}
        rows={tokens.map((t) => [
          <strong key={t.id}>{t.name}</strong>,
          t.scopes.join(", "),
          fmtTs(t.created_at),
          t.expires_at ? fmtTs(t.expires_at) : "never",
          <Badge key={t.id} value={t.revoked ? "revoked" : "active"} tone={t.revoked ? "bad" : "ok"} />,
          <button
            key={`r${t.id}`}
            className="danger small"
            disabled={t.revoked}
            onClick={() => {
              void del(`${paths.admin.tokens}/${t.id}`)
                .then(refresh)
                .catch((e) => setError(e instanceof Error ? e.message : String(e)));
            }}
          >
            Revoke
          </button>,
        ])}
      />
      <form className="row" style={{ marginTop: 12 }} onSubmit={create}>
        <label className="field">
          <span>New token name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <button type="submit">Create token (read scope)</button>
      </form>
    </div>
  );
}

function UserPanel() {
  const [users, setUsers] = useState<UserOut[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    get<UserOut[]>(paths.admin.users)
      .then(setUsers)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(refresh, [refresh]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await post(paths.admin.users, { username, password });
      setUsername("");
      setPassword("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>Users</h2>
      <ErrorBanner error={error} />
      <Table
        head={[["Username"], ["Full name"], ["Role"]]}
        rows={users.map((u) => [
          <strong key={u.id}>{u.username}</strong>,
          u.full_name,
          <Badge key={u.id} value={u.is_superadmin ? "superadmin" : "user"} tone={u.is_superadmin ? "info" : undefined} />,
        ])}
      />
      <form className="row" style={{ marginTop: 12 }} onSubmit={create}>
        <label className="field">
          <span>Username</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button type="submit">Create user</button>
      </form>
    </div>
  );
}

function AuditPanel() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  useEffect(() => {
    get<AuditRow[]>(`${paths.admin.audit}?limit=100`)
      .then(setRows)
      .catch(() => setRows([]));
  }, []);
  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>Audit trail</h2>
      <Table
        head={[["When"], ["User"], ["Action"], ["Detail"], ["IP"]]}
        rows={rows.map((r, i) => [
          <span key={i}>{fmtTs(r.ts)}</span>,
          r.username ?? "—",
          <code key={`a${i}`}>{r.action}</code>,
          r.detail ?? "—",
          r.ip ?? "—",
        ])}
      />
      {rows.length === 0 ? <p className="muted">No audit entries.</p> : null}
    </div>
  );
}
