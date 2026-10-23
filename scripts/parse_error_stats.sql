-- 解析错误分类统计（**只读**，可在生产直接跑）
-- 用法： sudo -n -u postgres psql -d greenhouse -P pager=off -f scripts/parse_error_stats.sql
--
-- 说明：007 未上线前，parse_error_stats / raw_messages.parse_error_kind 都不存在，
--       下面的查询会返回"表/列不存在"，这正是"1753 条无法分类"的现状；
--       007 上线并接线后，这里才会给出可解释的分类数字。
\pset pager off

\echo '===== 1. 007 是否已上线（结构探测） ====='
SELECT to_regclass('public.parse_error_stats')                               AS stats_table,
       (SELECT count(*) FROM information_schema.columns
         WHERE table_name='raw_messages' AND column_name='parse_error_kind') AS kind_column;

\echo '===== 2. 按分类汇总（聚合表，一张垃圾只占一行） ====='
SELECT kind,
       count(*)                AS signatures,
       sum(cnt)                AS occurrences,
       min(first_seen)         AS first_seen,
       max(last_seen)          AS last_seen
  FROM parse_error_stats
 GROUP BY kind ORDER BY occurrences DESC;

\echo '===== 3. 按设备 + 分类 + 指纹明细（看"到底是谁在发什么"） ====='
SELECT device_id, kind, signature, cnt, first_seen, last_seen,
       left(coalesce(last_detail,''), 80) AS detail
  FROM parse_error_stats
 ORDER BY cnt DESC LIMIT 20;

\echo '===== 4. raw_messages 里被拒报文的分类分布（逐条留存，受保留策略限制） ====='
SELECT parse_error_kind, count(*) AS rows,
       min(received_at) AS first_at, max(received_at) AS last_at
  FROM raw_messages
 WHERE parse_status = 'rejected'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '===== 5. 整体入库健康度：stored / rejected 各多少 ====='
SELECT parse_status, count(*) AS rows,
       min(received_at) AS first_at, max(received_at) AS last_at
  FROM raw_messages GROUP BY 1 ORDER BY 2 DESC;

\echo '===== 6. 现状兜底：007 未上线时，解析错误只能从服务日志数总数（不可分类） ====='
\echo '       journalctl -u greenhouse --since today --no-pager -o cat | grep -c "JSON解析失败"'
