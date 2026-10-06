"""Asterisk queue_log: SLA, wait, abandonment — the authoritative queue metric source.

queue_log.time is epoch seconds stored as string/number depending on version;
CAST(... AS UNSIGNED) normalizes it. Every query is bounded by :t0/:t1 so the
(unindexed) table scan stays small; see deploy/sql/optimize_issabel_indexes.sql
for an optional admin-run index.
"""

_AGG = """
SELECT queue AS queue,
       SUM(CASE WHEN event = 'ENTERQUEUE' THEN 1 ELSE 0 END) AS offered,
       SUM(CASE WHEN event IN ('CONNECT', 'BRIDGESTART') THEN 1 ELSE 0 END) AS answered,
       SUM(CASE WHEN event = 'ABANDON' THEN 1 ELSE 0 END) AS abandoned,
       SUM(CASE WHEN event IN ('COMPLETEAGENT', 'COMPLETECALLER')
                 THEN CAST(NULLIF(data1, '') AS UNSIGNED) ELSE 0 END) AS talk_total,
       AVG(CASE WHEN event = 'CONNECT' THEN CAST(NULLIF(data2, '') AS UNSIGNED) END) AS avg_wait,
       SUM(CASE WHEN event = 'CONNECT'
                 AND CAST(NULLIF(data2, '') AS UNSIGNED) <= %(sla_seconds)s
                THEN 1 ELSE 0 END) AS answered_within_sla
FROM {asterisk}.queue_log
WHERE CAST(time AS UNSIGNED) >= %(t0)s AND CAST(time AS UNSIGNED) < %(t1)s
  AND queue IS NOT NULL AND queue <> ''
GROUP BY queue
"""

SQL_QUEUE_STATS = _AGG

SQL_QUEUE_STATS_BY_BUCKET = """
SELECT {bucket_expr} AS bucket, queue AS queue,
       SUM(CASE WHEN event = 'ENTERQUEUE' THEN 1 ELSE 0 END) AS offered,
       SUM(CASE WHEN event IN ('CONNECT', 'BRIDGESTART') THEN 1 ELSE 0 END) AS answered,
       SUM(CASE WHEN event = 'ABANDON' THEN 1 ELSE 0 END) AS abandoned,
       AVG(CASE WHEN event = 'CONNECT' THEN CAST(NULLIF(data2, '') AS UNSIGNED) END) AS avg_wait,
       SUM(CASE WHEN event = 'CONNECT'
                 AND CAST(NULLIF(data2, '') AS UNSIGNED) <= %(sla_seconds)s
                THEN 1 ELSE 0 END) AS answered_within_sla
FROM {asterisk}.queue_log
WHERE CAST(time AS UNSIGNED) >= %(t0)s AND CAST(time AS UNSIGNED) < %(t1)s
  AND queue IS NOT NULL AND queue <> ''
GROUP BY bucket, queue
ORDER BY bucket
"""

BUCKET_DAY = "DATE(FROM_UNIXTIME(CAST(time AS UNSIGNED)))"
BUCKET_HOUR = "DATE_FORMAT(FROM_UNIXTIME(CAST(time AS UNSIGNED)), '%%Y-%%m-%%d %%H:00')"

SQL_ABANDONED_DETAIL = """
SELECT DATE_FORMAT(FROM_UNIXTIME(CAST(q.time AS UNSIGNED)), '%%Y-%%m-%%d %%H:%%i:%%s') AS ts,
       q.queue AS queue,
       CAST(NULLIF(q.data2, '') AS UNSIGNED) AS wait_seconds,
       COALESCE(e.data2, '') AS caller
FROM {asterisk}.queue_log q
LEFT JOIN {asterisk}.queue_log e
       ON e.callid = q.callid AND e.event = 'ENTERQUEUE'
      AND CAST(e.time AS UNSIGNED) >= %(t0)s
WHERE q.event = 'ABANDON'
  AND CAST(q.time AS UNSIGNED) >= %(t0)s AND CAST(q.time AS UNSIGNED) < %(t1)s
  AND (%(queue)s IS NULL OR q.queue = %(queue)s)
ORDER BY CAST(q.time AS UNSIGNED) DESC
LIMIT %(lim)s
"""
