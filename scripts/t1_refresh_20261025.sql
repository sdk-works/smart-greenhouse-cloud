-- T1 断流证据链刷新（2026-10-24 夜间）—— 纯只读，不得含任何 DML/DDL
-- 用法：sudo -n -u postgres psql -d greenhouse -P pager=off -f t1_refresh_20261024.sql
-- 与 t1_outage_20261023.sql 的区别：本份不写死日期，全部相对 now()，便于每夜复跑。

\echo '=========== A. 末条遥测与静默时长（口径：created_at 入库时刻） ==========='
-- 注意：不要用 max(msg_id) 取"末条"，那是**字典序最大值**，本库会取到 JY001/99/2
-- （设备时钟错一年的历史条），不是最后入库的那条。必须按 created_at 排序取第一行。
SELECT (SELECT msg_id FROM telemetry ORDER BY created_at DESC LIMIT 1) AS last_msg_id,
       max(created_at) AS last_created_at,
       round(extract(epoch from (now()-max(created_at))))::int AS silent_seconds,
       to_char(now(), 'YYYY-MM-DD HH24:MI') AS viewed_at
FROM telemetry;

\echo '=========== B. 近 4 天分天条数（看断流是否持续） ==========='
SELECT to_char(created_at,'YYYY-MM-DD') AS day,
       count(*) AS rows,
       min(created_at)::time AS first_at,
       max(created_at)::time AS last_at,
       min(msg_id) AS first_msg,
       max(msg_id) AS last_msg
FROM telemetry
WHERE created_at >= now() - interval '4 days'
GROUP BY 1 ORDER BY 1;

\echo '=========== C. 设备的 pageside 状态 vs 真实最后在线（陈旧状态风险） ==========='
SELECT device_id, status, last_seen,
       round(extract(epoch from (now()-last_seen)))::int AS stale_seconds
FROM devices
ORDER BY device_id;

\echo '=========== D. seq 连续性（boot 31 全窗口：应有=实有?） ==========='
WITH s AS (
  SELECT split_part(msg_id,'/',3)::int AS seq
  FROM telemetry
  WHERE split_part(msg_id,'/',2)='31'
)
SELECT count(*) AS rows,
       min(seq) AS seq_min,
       max(seq) AS seq_max,
       max(seq)-min(seq)+1 AS expected_if_contiguous,
       count(*) - (max(seq)-min(seq)+1) AS delta_rows_minus_expected
FROM s;

\echo '=========== E. boot 变化轨迹（每个 boot 的首末条） ==========='
SELECT split_part(msg_id,'/',2) AS boot,
       count(*) AS rows,
       min(split_part(msg_id,'/',3)::int) AS seq_min,
       max(split_part(msg_id,'/',3)::int) AS seq_max,
       min(created_at) AS first_at,
       max(created_at) AS last_at
FROM telemetry
GROUP BY 1 ORDER BY min(created_at);

\echo '=========== F. device_receipts 是否随遥测同步停止（ACK 回收停点） ==========='
SELECT to_char(created_at,'YYYY-MM-DD HH24') AS hh, count(*) AS receipts
FROM device_receipts
WHERE created_at >= now() - interval '4 days'
GROUP BY 1 ORDER BY 1;

\echo '=========== G. device_receipts 末条 vs telemetry 末条 ==========='
SELECT (SELECT max(created_at) FROM device_receipts) AS last_receipt,
       (SELECT max(created_at) FROM telemetry)       AS last_telemetry;

\echo '=========== H. raw_messages 落库情况（解析错误是否留存） ==========='
-- 修于 2026-10-25 夜间：原来写的是 status，而真实列名是 parse_status，
-- 这一节年年报错（ERROR: column "status" does not exist）却一直没被发现，
-- 因为 psql -f 遇到错误会继续往下跑，后面的 I 节照常出结果。
SELECT parse_status, count(*) AS rows, min(created_at) AS first_at, max(created_at) AS last_at
FROM raw_messages
GROUP BY 1 ORDER BY 1;

\echo '=========== H2. 原始报文留存覆盖率（解析失败的那部分有没有留下） ==========='
-- 这一节回答"1753 条解析错误能不能回溯分类"：
-- 只要 rejected/parse_error 类的行是 0，就说明失败报文从未落库，历史无法回溯。
SELECT (SELECT count(*) FROM telemetry)                     AS telemetry_rows,
       (SELECT count(*) FROM raw_messages)                  AS raw_rows,
       (SELECT count(*) FROM raw_messages
          WHERE parse_status <> 'stored')                   AS raw_non_stored,
       (SELECT count(*) FROM telemetry
          WHERE created_at >= (SELECT min(created_at) FROM raw_messages))
                                                            AS telemetry_since_raw_start;

\echo '=========== I. 遥测归属现状（004 未执行时的原始值） ==========='
SELECT attribution_status, count(*) FROM telemetry GROUP BY 1 ORDER BY 1;
