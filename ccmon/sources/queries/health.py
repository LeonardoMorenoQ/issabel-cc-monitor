"""Trunk/registration state from DB realtime tables (fallback when AMI is off).

PJSIP realtime stores contacts in ps_contacts when using realtime; if the table
is absent the capability probe marks trunk health unavailable and dependent
alert rules go to insufficient_data instead of firing false alerts.
"""

SQL_PS_CONTACTS = """
SELECT endpoint AS peer,
       CASE WHEN status = 'Online' THEN 'registered' ELSE 'unregistered' END AS status,
       COALESCE(status_reason, '') AS detail
FROM {asterisk}.ps_contacts
"""

# chan_sip legacy (Asterisk <= 13 / Issabel 4) — documented as untested for v1.
SQL_SIP_PEERS = """
SELECT name AS peer,
       CASE WHEN status LIKE 'OK%%' THEN 'registered' ELSE 'unregistered' END AS status,
       COALESCE(status, '') AS detail
FROM {asterisk}.sippeers
"""
