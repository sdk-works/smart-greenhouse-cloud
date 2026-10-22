-- ============================================================
-- 004 补充前置核对：设备采集时刻（sampled_at）可信性校验  **只读，可在生产执行**
-- 用法：psql -d greenhouse -f migrations/004_preflight_sampled_time_sanity.sql
--
-- 【为什么要这一份】004 迁移把 "sampled_at 存在" 直接判为一级证据（可信），
--   但 2026-10-22 夜间在生产实测到：同一台设备 JY001 在 2026-10-17 10:48 报的
--   两条消息 sampled_at = 2025-10-17 11:00:00（整整差一年），而 time_quality 同样是 1。
--   即：**time_quality=1 只说明"设备自称有时钟"，不能证明时钟是对的**。
--   如果不做偏差校验，这类错时钟行会被迁移判成"可信"并写死 resolved。
--   本脚本给出偏差分布与超阈值的具体行，供执行前决定是否加护栏。
--
-- 建议阈值：|sampled_at - 服务器接收时刻| <= 300 秒才算可信；
--   超阈值的一律降级为 pending/reason=legacy_sampled_time_implausible，不用 receive time 兜底。
-- ============================================================
\pset pager off

\echo == A. 全表 sampled_at 与接收时刻的偏差分布 ==
-- 说明：telemetry."timestamp" = 服务器收到报文的时刻（已由 004_preflight §6 证实，
--       与 created_at 相差 1~4ms），可作为参照基准。
SELECT count(*) AS rows_with_sampled_at,
       count(*) FILTER (WHERE abs(extract(epoch FROM (sampled_at - "timestamp"))) <= 60)  AS dev_le_60s,
       count(*) FILTER (WHERE abs(extract(epoch FROM (sampled_at - "timestamp"))) <= 300) AS dev_le_300s,
       count(*) FILTER (WHERE abs(extract(epoch FROM (sampled_at - "timestamp"))) > 300)  AS dev_gt_300s,
       min(extract(epoch FROM (sampled_at - "timestamp")))::numeric(14,1) AS min_dev_s,
       max(extract(epoch FROM (sampled_at - "timestamp")))::numeric(14,1) AS max_dev_s
  FROM telemetry
 WHERE sampled_at IS NOT NULL;

\echo == B. 偏差超阈值的具体行（这些行不应被判为可信） ==
SELECT id, msg_id, "timestamp" AS received_at, sampled_at, time_quality,
       extract(epoch FROM (sampled_at - "timestamp"))::numeric(14,1) AS dev_s,
       attribution_status
  FROM telemetry
 WHERE sampled_at IS NOT NULL
   AND abs(extract(epoch FROM (sampled_at - "timestamp"))) > 300
 ORDER BY sampled_at;

\echo == C. 本次迁移范围内（attribution_status IS NULL）的分级复核 ==
-- 把 004_preflight §10 的四个"可信"名额逐个摊开，看有没有被错时钟污染
SELECT id, msg_id, sampled_at, time_quality, "timestamp" AS received_at,
       extract(epoch FROM (sampled_at - "timestamp"))::numeric(14,1) AS dev_s,
       CASE WHEN abs(extract(epoch FROM (sampled_at - "timestamp"))) <= 300
            THEN '可信(偏差在阈值内)' ELSE '不可信(应降级)' END AS verdict
  FROM telemetry
 WHERE attribution_status IS NULL AND sampled_at IS NOT NULL
 ORDER BY sampled_at;

\echo == D. 加上偏差护栏后的预期改动量（与 004_preflight §10 对比用） ==
WITH t AS (
  SELECT *, coalesce(sampled_at, "timestamp") AS ev_time,
         CASE WHEN sampled_at IS NOT NULL
                    AND abs(extract(epoch FROM (sampled_at - "timestamp"))) <= 300
              THEN sampled_at ELSE NULL END AS trusted_sample_time
    FROM telemetry WHERE attribution_status IS NULL
), w AS (
  SELECT t.*, (SELECT count(*) FROM device_bindings b
                WHERE b.device_id = t.device_id AND b.greenhouse_id = t.greenhouse_id
                  AND b.start_at <= t.ev_time
                  AND (b.end_at IS NULL OR b.end_at >= t.ev_time)) AS win_cnt
    FROM t
)
SELECT 'resolved/backfill_sample_time' AS outcome, count(*) AS rows
  FROM w WHERE company_id IS NOT NULL AND ev_time IS NOT NULL AND win_cnt = 1
           AND trusted_sample_time IS NOT NULL
UNION ALL
SELECT 'resolved/backfill_receive_time_estimated', count(*)
  FROM w WHERE company_id IS NOT NULL AND ev_time IS NOT NULL AND win_cnt = 1
           AND trusted_sample_time IS NULL AND sampled_at IS NULL
UNION ALL
SELECT 'pending/legacy_sampled_time_implausible（新增护栏）', count(*)
  FROM w WHERE sampled_at IS NOT NULL AND trusted_sample_time IS NULL
UNION ALL
SELECT 'pending/legacy_before_attribution', count(*)
  FROM w WHERE ev_time IS NOT NULL AND company_id IS NOT NULL AND win_cnt = 0
           AND NOT (sampled_at IS NOT NULL AND trusted_sample_time IS NULL)
UNION ALL
SELECT 'pending/legacy_time_missing', count(*)
  FROM w WHERE ev_time IS NULL
UNION ALL
SELECT 'pending/conflict_binding_window', count(*)
  FROM w WHERE win_cnt > 1
UNION ALL
SELECT 'pending/no_company', count(*)
  FROM w WHERE company_id IS NULL;
