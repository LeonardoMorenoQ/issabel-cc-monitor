import { paths, type CampaignList } from "../api";
import {
  Badge,
  ErrorBanner,
  StaleBanner,
  Table,
  fmtInt,
  fmtTs,
  usePoll,
} from "../ui";

export function CampaignsPage() {
  const { data, error } = usePoll<CampaignList>(paths.live.campaigns, 5000);
  return (
    <>
      <h1>Campaigns</h1>
      <p className="sub">Dialer campaigns as reported by the call_center database.</p>
      <ErrorBanner error={error} />
      <StaleBanner stale={data?.stale} collectedAt={data?.collected_at} />
      <div className="panel table-wrap">
        <Table
          head={[
            ["Campaign"],
            ["Status"],
            ["Agents", true],
            ["Calls today", true],
            ["Connected today", true],
            ["Connect rate", true],
            ["Updated"],
          ]}
          rows={(data?.items ?? []).map((c) => [
            <code key={c.campaign}>{c.campaign}</code>,
            <Badge key={c.campaign} value={c.status} />,
            fmtInt(c.agents),
            fmtInt(c.calls_today),
            fmtInt(c.connected_today),
            c.calls_today
              ? `${((100 * n0(c.connected_today)) / n0(c.calls_today)).toFixed(1)}%`
              : "—",
            fmtTs(c.ts),
          ])}
        />
        {(data?.items ?? []).length === 0 ? (
          <p className="muted">
            No campaign data — requires the Issabel call_center database.
          </p>
        ) : null}
      </div>
    </>
  );
}

function n0(v: number | null | undefined): number {
  return v ?? 0;
}
