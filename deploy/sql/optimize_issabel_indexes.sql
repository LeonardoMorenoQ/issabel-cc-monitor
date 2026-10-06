-- =============================================================================
-- OPTIONAL performance indexes for ccmon report queries.
--
-- ccmon works without these; add them on busy systems (thousands of calls/day)
-- where /api/v1/reports/* feels slow. Every call is idempotent: existing
-- indexes and missing columns are skipped with a message. Requires SUPER or
-- CREATE privilege (run as root):
--
--   sudo mysql < deploy/sql/optimize_issabel_indexes.sql
--
-- NOTE: ccmon filters queue_log with CAST(time AS UNSIGNED), which a plain
-- index on `time` cannot serve directly; the index still helps ordering and
-- range scans on other tools. The CAST keeps ccmon correct across schema
-- variants (time stored as int vs. varchar) at the cost of index use.
-- =============================================================================

DELIMITER $$

DROP PROCEDURE IF EXISTS ccmon_idx$$
CREATE PROCEDURE ccmon_idx(
    IN p_db VARCHAR(64), IN p_tbl VARCHAR(64),
    IN p_idx VARCHAR(64), IN p_col VARCHAR(64)
)
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_db AND table_name = p_tbl AND column_name = p_col
    ) THEN
        SELECT CONCAT('skip (no column): ', p_db, '.', p_tbl, '.', p_col) AS msg;
    ELSEIF EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = p_db AND table_name = p_tbl AND index_name = p_idx
    ) THEN
        SELECT CONCAT('skip (exists): ', p_db, '.', p_tbl, '.', p_idx) AS msg;
    ELSE
        SET @s = CONCAT('CREATE INDEX `', p_idx, '` ON `', p_db, '`.`', p_tbl, '` (`', p_col, '`)');
        PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
        SELECT CONCAT('created: ', p_db, '.', p_tbl, '.', p_idx) AS msg;
    END IF;
END$$

DELIMITER ;

-- CDR history (date-range reports) ------------------------------------------
CALL ccmon_idx('asteriskcdrdb', 'cdr', 'idx_ccmon_cdr_calldate', 'calldate');
CALL ccmon_idx('asteriskcdrdb', 'cdr', 'idx_ccmon_cdr_disposition', 'disposition');

-- queue_log (SLA / wait / abandoned rolling windows) ------------------------
CALL ccmon_idx('asterisk', 'queue_log', 'idx_ccmon_queue_log_time', 'time');
CALL ccmon_idx('asterisk', 'queue_log', 'idx_ccmon_queue_log_event', 'event');

-- Call-center module (campaign + agent daily counters) ----------------------
CALL ccmon_idx('call_center', 'campaign_entry', 'idx_ccmon_centry_datetime', 'datetime_init');
CALL ccmon_idx('call_center', 'campaign_entry', 'idx_ccmon_centry_campaign', 'id_campaign');
CALL ccmon_idx('call_center', 'calls', 'idx_ccmon_calls_campaign', 'id_campaign');
CALL ccmon_idx('call_center', 'calls', 'idx_ccmon_calls_start_time', 'start_time');
CALL ccmon_idx('call_center', 'calls', 'idx_ccmon_calls_fecha', 'fecha_llamada');
CALL ccmon_idx('call_center', 'audit', 'idx_ccmon_audit_datetime_end', 'datetime_end');
CALL ccmon_idx('call_center', 'audit', 'idx_ccmon_audit_id_break', 'id_break');

DROP PROCEDURE ccmon_idx;
