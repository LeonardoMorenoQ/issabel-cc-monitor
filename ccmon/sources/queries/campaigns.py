"""Issabel Call Center campaigns — column-tolerant (schemas vary by release).

Capabilities probes which columns exist and only then are they selected, so an
unknown schema degrades to an empty/limited result instead of SQL errors.
"""

# Candidate column names per logical field, probed via information_schema.
CAMPAIGN_NAME_COLS = ("name", "title", "campaign")
CAMPAIGN_STATUS_COLS = ("status", "state", "estatus")
CALLS_TIME_COLS = ("start_time", "fecha_llamada", "datetime_originate", "datetime_entry_queue")


def build_campaign_query(name_col: str, status_col: str | None, calls_time_col: str | None) -> str:
    """SELECT with per-campaign today counters; only known columns are used."""
    status_expr = ("COALESCE(c." + status_col + ", '')") if status_col else "''"
    if calls_time_col:
        sub = (
            "SELECT COUNT(*) FROM {call_center}.calls x "
            "WHERE x.id_campaign = c.id AND COALESCE(x." + calls_time_col + ", '') >= CURDATE()"
        )
        sub_conn = (
            "SELECT COUNT(*) FROM {call_center}.calls x "
            "WHERE x.id_campaign = c.id AND COALESCE(x." + calls_time_col + ", '') >= CURDATE() "
            "AND (CAST(NULLIF(x.duration, '') AS UNSIGNED) > 0 "
            "     OR x.status LIKE '%ANSWER%' OR x.status LIKE '%Success%')"
        )
    else:
        sub, sub_conn = "SELECT 0", "SELECT 0"
    return (
        "SELECT c.id AS id, "
        "c." + name_col + " AS campaign, " + status_expr + " AS status, "
        "(" + sub + ") AS calls_today, (" + sub_conn + ") AS connected_today "
        "FROM {call_center}.campaign c ORDER BY 2"
    )


SQL_CAMPAIGN_ENTRY_TODAY = """
SELECT id_campaign AS campaign_id, COUNT(*) AS calls_today
FROM {call_center}.campaign_entry
WHERE datetime_init >= CURDATE()
GROUP BY id_campaign
"""
