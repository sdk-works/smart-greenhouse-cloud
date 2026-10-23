-- T1 断流证据链（2026-10-23）—— 纯只读，不得含任何 DML/DDL
-- 用法：sudo -n -u postgres psql -d greenhouse -P pager=off -f t1_outage_20261023.sql

\echo '=========== A. 今天(2026-10-23)遥测分小时条数 ==========='
SELECT to_char(created_at,'MM-DD HH24') AS hh,
       count(*) AS rows,
       min(created_at)::time AS first_at,
       max(created_at)::time AS last_at,
       min(split_part(msg_id,'/',3)::int) AS seq_min,
       max(split_part(msg_id,'/',3)::int) AS seq_max
FROM telemetry
WHERE created_at >= '2026-10-23 00:00:00+08'
GROUP BY 1 ORDER BY 1;

\echo '=========== B. 08:00 之后全部遥测条目（按时间倒序前 40） ==========='
SELECT created_at, msg_id, split_part(msg_id,'/',2) AS boot, split_part(msg_id,'/',3)::int AS seq,
       temperature, humidity, co2_level, time_quality, attribution_status
FROM telemetry
WHERE created_at >= '2026-10-23 08:00:00+08'
ORDER BY created_at DESC LIMIT 40;

\echo '=========== C. 08:00 之后相邻间隔分布（秒） ==========='
WITH t AS (
  SELECT created_at,
         lag(created_at) OVER (ORDER BY created_at) AS prev
  FROM telemetry WHERE created_at >= '2026-10-23 08:00:00+08'
)
SELECT round(extract(epoch from (created_at-prev)))::int AS gap_s, count(*) AS n
FROM t WHERE prev IS NOT NULL
GROUP BY 1 ORDER BY 1;

\echo '=========== D. 最后 3 小时(06:00起)相邻间隔分布（秒，含断流前基准） ==========='
WITH t AS (
  SELECT created_at, lag(created_at) OVER (ORDER BY created_at) AS prev
  FROM telemetry WHERE created_at >= '2026-10-23 06:00:00+08'
)
SELECT CASE WHEN round(extract(epoch from (created_at-prev)))::int <= 120
            THEN round(extract(epoch from (created_at-prev)))::int::text
            ELSE '>120' END AS gap_s, count(*) AS n
FROM t WHERE prev IS NOT NULL GROUP BY 1 ORDER BY 1;

\echo '=========== E. seq 连续性（boot 31，今天） ==========='
WITH s AS (
  SELECT split_part(msg_id,'/',3)::int AS seq, created_at
  FROM telemetry
  WHERE created_at >= '2026-10-23 00:00:00+08' AND split_part(msg_id,'/',2)='31'
)
SELECT count(*) AS rows, min(seq) AS seq_min, max(seq) AS seq_max,
       max(seq)-min(seq)+1 AS expected_if_contiguous,
       count(*)-(max(seq)-min(seq)+1) AS delta,
       count(DISTINCT seq) AS distinct_seq
FROM s;

\echo '=========== E2. boot 31 的 seq 缺口明细（如有） ==========='
WITH s AS (
  SELECT split_part(msg_id,'/',3)::int AS seq, created_at,
         lag(split_part(msg_id,'/',3)::int) OVER (ORDER BY split_part(msg_id,'/',3)::int) AS prev_seq
  FROM telemetry WHERE created_at >= '2026-10-23 00:00:00+08' AND split_part(msg_id,'/',2)='31'
)
SELECT prev_seq, seq, seq-prev_seq AS hole, created_at
FROM s WHERE prev_seq IS NOT NULL AND seq-prev_seq <> 1 ORDER BY seq;

\echo '=========== F. boot_id 轨迹（最近 10 个 boot） ==========='
SELECT split_part(msg_id,'/',2) AS boot, count(*) AS rows,
       min(split_part(msg_id,'/',3)::int) AS seq_min,
       max(split_part(msg_id,'/',3)::int) AS seq_max,
       min(created_at) AS first_seen, max(created_at) AS last_seen
FROM telemetry
GROUP BY 1 ORDER BY min(created_at) DESC LIMIT 10;

\echo '=========== G. 最后一条完整字段值 ==========='
SELECT * FROM telemetry ORDER BY created_at DESC LIMIT 1;

\echo '=========== H. device_receipts ACK（今天分小时） ==========='
SELECT to_char(created_at,'MM-DD HH24') AS hh, count(*) AS acks,
       min(created_at)::time AS first_at, max(created_at)::time AS last_at
FROM device_receipts
WHERE created_at >= '2026-10-23 00:00:00+08'
GROUP BY 1 ORDER BY 1;

\echo '=========== H2. ACK 最后 10 条 + 未回收上行（今天 08:00 后） ==========='
SELECT msg_id, kind, created_at FROM device_receipts ORDER BY created_at DESC LIMIT 10;

SELECT m.msg_id, m.received_at, m.parse_status
FROM raw_messages m
LEFT JOIN device_receipts r ON r.msg_id = m.msg_id
WHERE m.received_at >= '2026-10-23 08:00:00+08' AND r.msg_id IS NULL
ORDER BY m.received_at DESC LIMIT 20;

\echo '=========== I. raw_messages 今天分小时（含解析状态） ==========='
SELECT to_char(received_at,'MM-DD HH24') AS hh, parse_status, count(*) AS n,
       max(received_at)::time AS last_at
FROM raw_messages WHERE received_at >= '2026-10-23 00:00:00+08'
GROUP BY 1,2 ORDER BY 1,2;

\echo '=========== J. raw_messages 最后 5 条（任意状态） ==========='
SELECT received_at, device_id, msg_id, parse_status, payload_bytes, left(error,60) AS err
FROM raw_messages ORDER BY received_at DESC LIMIT 5;

\echo '=========== K. 全表最后活动时刻汇总 ==========='
SELECT 'telemetry' AS tbl, max(created_at) AS last_activity FROM telemetry
UNION ALL SELECT 'raw_messages', max(received_at) FROM raw_messages
UNION ALL SELECT 'device_receipts', max(created_at) FROM device_receipts
UNION ALL SELECT 'images', max(created_at) FROM images
UNION ALL SELECT 'audit_logs', max(created_at) FROM audit_logs;

\echo '=========== L. 停传时长（截至执行时刻） ==========='
SELECT now() AS now_ts,
       (SELECT max(created_at) FROM telemetry) AS last_telemetry,
       round(extract(epoch from (now() - (SELECT max(created_at) FROM telemetry)))/60)::int AS silence_minutes;
