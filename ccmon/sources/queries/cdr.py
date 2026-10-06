"""asteriskcdrdb.cdr: traffic/answer-rate reports (per-version stable schema)."""

SQL_CDR_SUMMARY = """
SELECT COUNT(*) AS total,
       SUM(CASE WHEN disposition IN ('ANSWER', 'ANSWERED') THEN 1 ELSE 0 END) AS answered,
       AVG(duration) AS avg_duration
FROM {cdr}.cdr
WHERE calldate >= %(t0)s AND calldate < %(t1)s
"""

SQL_CDR_BY_DISPOSITION = """
SELECT disposition AS disposition, COUNT(*) AS total
FROM {cdr}.cdr
WHERE calldate >= %(t0)s AND calldate < %(t1)s
GROUP BY disposition
ORDER BY total DESC
"""

SQL_CDR_HOURLY = """
SELECT DATE_FORMAT(calldate, '%%Y-%%m-%%d %%H:00') AS bucket,
       COUNT(*) AS total,
       SUM(CASE WHEN disposition IN ('ANSWER', 'ANSWERED') THEN 1 ELSE 0 END) AS answered,
       AVG(duration) AS avg_duration
FROM {cdr}.cdr
WHERE calldate >= %(t0)s AND calldate < %(t1)s
GROUP BY bucket
ORDER BY bucket
"""
