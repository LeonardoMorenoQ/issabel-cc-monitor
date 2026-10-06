"""Agent sessions, pauses and productivity (Issabel Call Center `call_center`).

Reference mapping (verified from the repo's views):
  session = audit row with id_break IS NULL  and datetime_end IS NULL
  pause   = audit row with id_break NOT NULL and datetime_end IS NULL
"""

SQL_OPEN_SESSIONS = """
SELECT a.number AS agent, a.name AS name, COALESCE(au.login_extension, '') AS extension,
       au.datetime_init AS session_start
FROM {call_center}.audit au
JOIN {call_center}.agent a ON a.id = au.id_agent
WHERE au.id_break IS NULL AND au.datetime_end IS NULL
"""

SQL_OPEN_PAUSES = """
SELECT a.number AS agent, au.datetime_init AS pause_start, COALESCE(b.name, '') AS reason
FROM {call_center}.audit au
JOIN {call_center}.agent a ON a.id = au.id_agent
LEFT JOIN {call_center}.`break` b ON b.id = au.id_break
WHERE au.id_break IS NOT NULL AND au.datetime_end IS NULL
"""

SQL_HANDLED_TODAY = """
SELECT a.number AS agent, COUNT(*) AS handled
FROM {call_center}.call_entry ce
JOIN {call_center}.agent a ON a.id = ce.id_agent
WHERE ce.datetime_init IS NOT NULL AND ce.datetime_init >= CURDATE()
GROUP BY a.number
"""

# All known agents (for offline reporting of agents with no open session).
SQL_ALL_AGENTS = """
SELECT a.number AS agent, a.name AS name, a.estatus AS estatus
FROM {call_center}.agent a
"""

SQL_AGENT_STATS_RANGE = """
SELECT a.number AS agent, COUNT(*) AS answered,
       AVG(NULLIF(ce.duration_wait, '')) AS avg_wait,
       SUM(COALESCE(NULLIF(ce.duration, ''), 0)) AS talk
FROM {call_center}.call_entry ce
JOIN {call_center}.agent a ON a.id = ce.id_agent
WHERE ce.datetime_init >= %(t0)s AND ce.datetime_init < %(t1)s
GROUP BY a.number
ORDER BY answered DESC
LIMIT %(lim)s
"""
