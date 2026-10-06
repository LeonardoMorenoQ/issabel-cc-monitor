"""Live queue state — AMI is preferred; these are the read-only DB fallbacks.

Column/table names were verified against the reference repo's operational
views (vw_cc_llamadas_entrantes uses call_entry + queue_call_entry.queue).
Placeholders are PyMySQL style (%(name)s).
"""

# Active (being answered) and waiting calls per queue from call_center.
SQL_ACTIVE_CALLS_PER_QUEUE = """
SELECT q.queue AS queue, COUNT(*) AS active
FROM {call_center}.call_entry ce
JOIN {call_center}.queue_call_entry q ON q.id = ce.id_queue_call_entry
WHERE ce.datetime_init IS NOT NULL AND ce.datetime_end IS NULL
GROUP BY q.queue
"""

SQL_WAITING_CALLS_PER_QUEUE = """
SELECT q.queue AS queue, COUNT(*) AS waiting
FROM {call_center}.call_entry ce
JOIN {call_center}.queue_call_entry q ON q.id = ce.id_queue_call_entry
WHERE ce.datetime_init IS NULL AND ce.datetime_entry_queue IS NOT NULL
GROUP BY q.queue
"""

# Asterisk-level member state: totals and paused counts per queue.
SQL_QUEUE_MEMBER_COUNTS = """
SELECT queue_name AS queue, COUNT(*) AS members,
       SUM(CASE WHEN paused = 1 THEN 1 ELSE 0 END) AS paused
FROM {asterisk}.queue_member
GROUP BY queue_name
"""

SQL_QUEUE_MEMBER_PAUSED = """
SELECT queue_name AS queue, name AS device, CAST(paused AS UNSIGNED) AS paused
FROM {asterisk}.queue_member
"""

# Distinct queue names known to the call center module (for fallback listing).
SQL_QUEUE_NAMES = """
SELECT DISTINCT q.queue AS queue
FROM {call_center}.queue_call_entry q
WHERE q.queue IS NOT NULL AND q.queue <> ''
"""
