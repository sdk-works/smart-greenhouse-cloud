-- ============================================================
-- 004 迁移前置核对（**只读**，可在生产直接执行）
-- 目的：执行回填前先给出"分组数量 + 抽样证据 + 预期改动量"
-- 用法：psql -d greenhouse -f migrations/004_preflight.sql
-- ============================================================
\pset pager off

\echo == 1. attribution_status 全量分布 ==
select coalesce(attribution_status, '<NULL>') as status, count(*),
       min(created_at) as first_row, max(created_at) as last_row
  from telemetry group by 1 order by 2 desc;

\echo == 2. NULL 行关键维度是否齐全（缺一即视为证据不足） ==
select count(*)                                    as null_rows,
       count(*) filter (where company_id    is null) as company_null,
       count(*) filter (where greenhouse_id is null) as greenhouse_null,
       count(*) filter (where device_id     is null) as device_null,
       count(distinct device_id)                  as devices,
       count(distinct greenhouse_id)              as greenhouses
  from telemetry where attribution_status is null;

\echo == 3. NULL 行按 (公司, 棚室, 设备) 分组 ==
select company_id, greenhouse_id, device_id, count(*) as rows,
       min(created_at) as t0, max(created_at) as t1
  from telemetry where attribution_status is null
 group by 1,2,3 order by 4 desc;

\echo == 4. 证据分级：有绑定窗口覆盖 / 无覆盖 / 公司为空 ==
select
  count(*) as null_rows,
  count(*) filter (where company_id is null) as no_company,
  count(*) filter (where company_id is not null and exists (
      select 1 from device_bindings b
       where b.device_id = t.device_id and b.greenhouse_id = t.greenhouse_id
         and b.start_at <= t.created_at
         and (b.end_at is null or b.end_at >= t.created_at))) as covered_by_binding,
  count(*) filter (where company_id is not null and not exists (
      select 1 from device_bindings b
       where b.device_id = t.device_id and b.greenhouse_id = t.greenhouse_id
         and b.start_at <= t.created_at
         and (b.end_at is null or b.end_at >= t.created_at))) as no_binding
  from telemetry t where t.attribution_status is null;

\echo == 5. 跨公司风险：NULL 行的 company 与棚室所属公司是否一致 ==
select count(*) as null_rows,
       count(*) filter (where g.id is null) as greenhouse_missing,
       count(*) filter (where g.company_id is distinct from t.company_id) as company_mismatch
  from telemetry t left join greenhouses g on g.id = t.greenhouse_id
 where t.attribution_status is null;

\echo == 6. 抽样：每个分组 2 条（人工核对用） ==
select device_id, greenhouse_id, company_id, id, timestamp, msg_id, created_at
  from (select t.*, row_number() over (
          partition by device_id, greenhouse_id, company_id order by created_at) rn
          from telemetry t where attribution_status is null) s
 where rn <= 2 order by device_id, created_at;

\echo == 7. 预期改动量（迁移将写入 attribution_migrations） ==
select 'resolved/backfill_binding_window' as outcome, count(*) as rows
  from telemetry t
 where t.attribution_status is null and t.company_id is not null
   and exists (select 1 from device_bindings b
                where b.device_id=t.device_id and b.greenhouse_id=t.greenhouse_id
                  and b.start_at<=t.created_at
                  and (b.end_at is null or b.end_at>=t.created_at))
union all
select 'pending/legacy_before_attribution', count(*)
  from telemetry t
 where t.attribution_status is null
   and (t.company_id is null or not exists (
        select 1 from device_bindings b
         where b.device_id=t.device_id and b.greenhouse_id=t.greenhouse_id
           and b.start_at<=t.created_at
           and (b.end_at is null or b.end_at>=t.created_at)));
