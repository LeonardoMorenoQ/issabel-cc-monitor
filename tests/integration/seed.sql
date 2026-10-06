-- Minimal Issabel-like schema for ccmon integration tests.
-- Loaded automatically by the MariaDB docker entrypoint (seed.sql in
-- /docker-entrypoint-initdb.d/). `asteriskcdrdb` is pre-created via
-- MARIADB_DATABASE. Timestamps are relative to NOW() so "today" queries hit.
-- This file is TEST scaffolding only — never run it on a production PBX.

CREATE DATABASE IF NOT EXISTS `asterisk`;
CREATE DATABASE IF NOT EXISTS `call_center`;

-- ---------------------------------------------------------------- asteriskcdrdb
USE `asteriskcdrdb`;
CREATE TABLE cdr (
  calldate   DATETIME     NOT NULL,
  clid       VARCHAR(80)  DEFAULT '',
  src        VARCHAR(80)  DEFAULT '',
  dst        VARCHAR(80)  DEFAULT '',
  dcontext   VARCHAR(80)  DEFAULT '',
  channel    VARCHAR(80)  DEFAULT '',
  dstchannel VARCHAR(80)  DEFAULT '',
  lastapp    VARCHAR(32)  DEFAULT '',
  lastdata   VARCHAR(80)  DEFAULT '',
  duration   INT          DEFAULT 0,
  billsec    INT          DEFAULT 0,
  disposition VARCHAR(32) DEFAULT '',
  uniqueid   VARCHAR(32)  DEFAULT ''
);
INSERT INTO cdr (calldate, src, dst, duration, disposition)
VALUES
  (NOW() - INTERVAL 2 HOUR, '101', '800', 120, 'ANSWERED'),
  (NOW() - INTERVAL 3 HOUR, '102', '800', 45,  'ANSWERED'),
  (NOW() - INTERVAL 4 HOUR, '103', '900', 0,   'FAILED'),
  (NOW() - INTERVAL 1 DAY,  '104', '801', 300, 'ANSWERED');

-- ---------------------------------------------------------------------- asterisk
USE `asterisk`;
CREATE TABLE queue_log (
  time    VARCHAR(20)  NOT NULL,           -- epoch as string (variant support)
  callid  VARCHAR(80)  NOT NULL DEFAULT '',
  uniqueid VARCHAR(32) NOT NULL DEFAULT '',
  queue   VARCHAR(128) DEFAULT NULL,
  event   VARCHAR(32)  DEFAULT NULL,
  data1   VARCHAR(128) DEFAULT '',
  data2   VARCHAR(128) DEFAULT '',
  data3   VARCHAR(128) DEFAULT '',
  data4   VARCHAR(128) DEFAULT ''
);
CREATE INDEX idx_time ON queue_log (time);

-- queue 800: 5 offered, 3 answered (waits 5/12/40s), 2 abandoned (60/90s)
INSERT INTO queue_log (time, callid, queue, event, data1, data2) VALUES
  (UNIX_TIMESTAMP() - 300, 'c1', '800', 'ENTERQUEUE', '800', '5511000'),
  (UNIX_TIMESTAMP() - 295, 'c1', '800', 'CONNECT',    '101', '5'),
  (UNIX_TIMESTAMP() - 260, 'c1', '800', 'COMPLETEAGENT', '10', ''),
  (UNIX_TIMESTAMP() - 280, 'c2', '800', 'ENTERQUEUE', '800', '5511001'),
  (UNIX_TIMESTAMP() - 268, 'c2', '800', 'CONNECT',    '102', '12'),
  (UNIX_TIMESTAMP() - 250, 'c2', '800', 'COMPLETEAGENT', '18', ''),
  (UNIX_TIMESTAMP() - 240, 'c3', '800', 'ENTERQUEUE', '800', '5511002'),
  (UNIX_TIMESTAMP() - 200, 'c3', '800', 'CONNECT',    '101', '40'),
  (UNIX_TIMESTAMP() - 160, 'c3', '800', 'COMPLETEAGENT', '40', ''),
  (UNIX_TIMESTAMP() - 220, 'c4', '800', 'ENTERQUEUE', '800', '5511003'),
  (UNIX_TIMESTAMP() - 160, 'c4', '800', 'ABANDON',    '', '60'),
  (UNIX_TIMESTAMP() - 100, 'c5', '800', 'ENTERQUEUE', '800', '5511004'),
  (UNIX_TIMESTAMP() -  10, 'c5', '800', 'ABANDON',    '', '90'),
-- queue 801: 2 offered, 1 answered, 1 abandoned
  (UNIX_TIMESTAMP() - 260, 'd1', '801', 'ENTERQUEUE', '801', '5522000'),
  (UNIX_TIMESTAMP() - 255, 'd1', '801', 'CONNECT',    '104', '5'),
  (UNIX_TIMESTAMP() - 245, 'd1', '801', 'COMPLETEAGENT', '10', ''),
  (UNIX_TIMESTAMP() - 230, 'd2', '801', 'ENTERQUEUE', '801', '5522001'),
  (UNIX_TIMESTAMP() - 200, 'd2', '801', 'ABANDON',    '', '30');

CREATE TABLE queue_member (
  membername  VARCHAR(128) DEFAULT '',
  name        VARCHAR(128) NOT NULL,
  queue_name  VARCHAR(128) NOT NULL,
  paused      TINYINT      NOT NULL DEFAULT 0
);
INSERT INTO queue_member (membername, name, queue_name, paused) VALUES
  ('101', 'PJSIP/101', '800', 0),
  ('102', 'PJSIP/102', '800', 0),
  ('103', 'PJSIP/103', '800', 1),
  ('104', 'PJSIP/104', '801', 0);

CREATE TABLE sippeers (
  name   VARCHAR(128) NOT NULL,
  status VARCHAR(64)  DEFAULT ''
);
INSERT INTO sippeers (name, status) VALUES ('siptrunk', 'OK (valid)');

-- ------------------------------------------------------------------ call_center
USE `call_center`;
CREATE TABLE agent (
  id     INT PRIMARY KEY AUTO_INCREMENT,
  number VARCHAR(32)  NOT NULL,
  name   VARCHAR(128) DEFAULT '',
  estatus VARCHAR(32) DEFAULT 'Active'
);
INSERT INTO agent (number, name) VALUES
  ('101', 'Alice'),
  ('102', 'Bob'),
  ('103', 'Carol'),
  ('104', 'Dave');

CREATE TABLE `break` (
  id   INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(64) NOT NULL
);
INSERT INTO `break` (name) VALUES ('Lunch'), ('Coffee');

CREATE TABLE audit (
  id              INT PRIMARY KEY AUTO_INCREMENT,
  id_agent        INT NOT NULL,
  id_break        INT NULL,               -- NULL => session, NOT NULL => pause
  login_extension VARCHAR(32) DEFAULT '',
  datetime_init   DATETIME NOT NULL,
  datetime_end    DATETIME NULL
);
INSERT INTO audit (id_agent, id_break, login_extension, datetime_init, datetime_end)
VALUES
  (1, NULL, '1001', NOW() - INTERVAL 4 HOUR, NULL),   -- open session: Alice
  (2, NULL, '1002', NOW() - INTERVAL 3 HOUR, NULL),   -- open session: Bob
  (3, NULL, '1003', NOW() - INTERVAL 5 HOUR, NULL),   -- Carol: session row...
  (3, 1,    '1003', NOW() - INTERVAL 42 MINUTE, NULL), -- ...and open pause (Lunch)
  (4, NULL, '1004', NOW() - INTERVAL 6 HOUR, NOW() - INTERVAL 1 HOUR); -- closed

CREATE TABLE queue_call_entry (
  id    INT PRIMARY KEY AUTO_INCREMENT,
  queue VARCHAR(32) NOT NULL
);
INSERT INTO queue_call_entry (queue) VALUES ('800'), ('801');

CREATE TABLE call_entry (
  id                   INT PRIMARY KEY AUTO_INCREMENT,
  id_agent             INT NULL,
  id_queue_call_entry  INT NULL,
  datetime_init        DATETIME NULL,
  datetime_end         DATETIME NULL,
  datetime_entry_queue DATETIME NULL,
  duration             VARCHAR(16) DEFAULT '',
  duration_wait        VARCHAR(16) DEFAULT '',
  status               VARCHAR(32) DEFAULT ''
);
INSERT INTO call_entry
  (id_agent, id_queue_call_entry, datetime_init, datetime_end, datetime_entry_queue, duration, duration_wait, status)
VALUES
  (1, 1, NOW() - INTERVAL 1 HOUR, NOW() - INTERVAL 59 MINUTE, NOW() - INTERVAL 61 MINUTE, '60', '12', 'ANSWER'),
  (2, 1, NOW() - INTERVAL 2 HOUR, NOW() - INTERVAL 119 MINUTE, NOW() - INTERVAL 121 MINUTE, '45', '8',  'ANSWER'),
  (1, 2, NULL, NULL, NOW() - INTERVAL 5 MINUTE, '', '0', ''),             -- waiting
  (NULL, 1, NOW() - INTERVAL 30 MINUTE, NOW() - INTERVAL 29 MINUTE, NOW() - INTERVAL 31 MINUTE, '30', '60', 'ABANDON');

CREATE TABLE campaign (
  id     INT PRIMARY KEY AUTO_INCREMENT,
  name   VARCHAR(64) NOT NULL,
  status VARCHAR(32) DEFAULT ''
);
INSERT INTO campaign (name, status) VALUES ('Promo Dialer', 'Active');

CREATE TABLE campaign_entry (
  id_campaign  INT NOT NULL,
  datetime_init DATETIME NOT NULL
);
INSERT INTO campaign_entry (id_campaign, datetime_init)
VALUES (1, NOW() - INTERVAL 10 MINUTE), (1, NOW() - INTERVAL 20 MINUTE);

CREATE TABLE calls (
  id_campaign INT NOT NULL,
  start_time  DATETIME NULL,
  duration    VARCHAR(16) DEFAULT '',
  status      VARCHAR(32) DEFAULT ''
);
INSERT INTO calls (id_campaign, start_time, duration, status)
VALUES
  (1, NOW() - INTERVAL 10 MINUTE, '30', 'ANSWER'),
  (1, NOW() - INTERVAL 20 MINUTE, '0',  'NOANSWER'),
  (1, NOW() - INTERVAL 2 DAY,     '45', 'ANSWER');

-- --------------------------------------------------------------------- grants
-- MARIADB_USER only receives privileges on MARIADB_DATABASE; the test user
-- needs SELECT on the two databases this file creates as well (the app user
-- in production is SELECT-only; this test user is ALL on test DBs only).
GRANT ALL PRIVILEGES ON `asteriskcdrdb`.* TO 'ccmon_it'@'%';
GRANT ALL PRIVILEGES ON `asterisk`.*      TO 'ccmon_it'@'%';
GRANT ALL PRIVILEGES ON `call_center`.*   TO 'ccmon_it'@'%';
FLUSH PRIVILEGES;
