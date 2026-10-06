-- =============================================================================
-- ccmon read-only source user (Issabel/MariaDB)
--
-- Run ONCE as the database root on the PBX:
--   sudo mysql < deploy/sql/readonly_grants.sql
-- then set ISSABEL_DB_PASSWORD in /etc/ccmon/ccmon.env to the same password
-- you type below. ccmon never writes to Issabel/Asterisk databases; this user
-- physically cannot (SELECT only).
-- =============================================================================

-- !! EDIT BEFORE RUNNING !!
SET @ccmon_password = 'CHANGE_ME_READ_ONLY_PASSWORD';

CREATE USER IF NOT EXISTS 'ccmon_reader'@'127.0.0.1' IDENTIFIED BY @ccmon_password;
CREATE USER IF NOT EXISTS 'ccmon_reader'@'localhost'  IDENTIFIED BY @ccmon_password;

-- Databases ccmon reads:
--   asteriskcdrdb : CDR historical reports
--   asterisk      : queue_log (SLA/wait/abandoned), queue tables
--   call_center   : Issabel call-center module (agents, campaigns, audit)
GRANT SELECT ON `asteriskcdrdb`.* TO 'ccmon_reader'@'127.0.0.1';
GRANT SELECT ON `asteriskcdrdb`.* TO 'ccmon_reader'@'localhost';
GRANT SELECT ON `asterisk`.*      TO 'ccmon_reader'@'127.0.0.1';
GRANT SELECT ON `asterisk`.*      TO 'ccmon_reader'@'localhost';
GRANT SELECT ON `call_center`.*   TO 'ccmon_reader'@'127.0.0.1';
GRANT SELECT ON `call_center`.*   TO 'ccmon_reader'@'localhost';

FLUSH PRIVILEGES;

-- Verify: should list only SELECT privileges.
SHOW GRANTS FOR 'ccmon_reader'@'127.0.0.1';
