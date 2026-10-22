-- ============================================================
-- 004 迁移前置核对（**只读**，可在生产直接执行）
-- 目的：执行回填前先给出"分组数量 + 抽样证据 + 预期改动量"
-- 用法：psql -d greenhouse -f migrations/004_preflight.sql
--
-- 【时间语义】本系统 telemetry."timestamp" = 服务器接收时间（不是采集时间），
--   sampled_at 仅当 time_quality<>0 才有值。因此证据分两级：
--     一级（可信）：sampled_at 存在，按采集时刻判窗口
--     二级（推定）：只有接收时间，按收到报文的时刻判窗口
--   二级行会在迁移里标 reason=backfill_receive_time_estimated，可逐条复核。
-- ============================================================
\pset pager off

\echo == 1. attribution_status 全量分布 ==
select coalesce(attribution_status, '<NULL>') as status, count(*),
       min(created_at) as first_row, max(created_at) as last_row
  from telemetry group by 1 order by 2 desc;

\echo == 2. 迁移是否已执行过（记录表 + 结构，双证据） ==
select to_regclass('public.attribution_migrations') as migrations_table,
       (select count(*) from information_schema.columns
         where table_name='telemetry'
           and column_name in ('attribution_reason','binding_id','binding_version')) as new_cols;

\echo == 3. NULL 行关键维度是否齐全（缺一即视为证据不足） ==
select count(*)                                    as null_rows,
       count(*) filter (where company_id    is null) as company_null,
       count(*) filter (where greenhouse_id is null) as greenhouse_null,
       count(*) filter (where device_id     is null) as device_null,
       count(distinct device_id)                  as devices,
       count(distinct greenhouse_id)              as greenhouses
  from telemetry where attribution_status is null;

\echo == 4. NULL 行按 (公司, 棚室, 设备) 分组 ==
select company_id, greenhouse_id, device_id, count(*) as rows,
       min(created_at) as t0, max(created_at) as t1
  from telemetry where attribution_status is null
 group by 1,2,3 order by 4 desc;

\echo == 5. 时间证据分级（决定 reason 是可信还是推定） ==
select count(*) as null_rows,
       count(*) filter (where sampled_at is not null)                     as has_sample_time,
       count(*) filter (where sampled_at is null and time_quality <> 0)   as tq_nonzero_no_sample,
       count(*) filter (where sampled_at is null and coalesce(time_quality,0) = 0) as receive_time_only,
       count(*) filter (where coalesce(sampled_at, "timestamp") is null)  as no_time_at_all
  from telemetry where attribution_status is null;

\echo == 6. 接收时间与入库时间的偏差（确认 timestamp 就是接收时刻） ==
select min(extract(epoch from (created_at - "timestamp")))::numeric(10,3) as min_lag_s,
       max(extract(epoch from (created_at - "timestamp")))::numeric(10,3) as max_lag_s,
       count(*) filter (where abs(extract(epoch from (created_at - "timestamp"))) > 60) as over_60s
  from telemetry where attribution_status is null;

\echo == 7. 窗口覆盖分组（按迁移同一时间基准） ==
with t as (
  select *, coalesce(sampled_at, "timestamp") as ev_time
    from telemetry where attribution_status is null
)
select
  count(*) as null_rows,
  count(*) filter (where company_id is null) as no_company,
  count(*) filter (where ev_time is null)    as no_time,
  count(*) filter (where company_id is not null and ev_time is not null and (
      select count(*) from device_bindings b
       where b.device_id=t.device_id and b.greenhouse_id=t.greenhouse_id
         and b.start_at <= t.ev_time
         and (b.end_at is null or b.end_at >= t.ev_time)) = 1) as exactly_one_window,
  count(*) filter (where company_id is not null and ev_time is not null and (
      select count(*) from device_bindings b
       where b.device_id=t.device_id and b.greenhouse_id=t.greenhouse_id
         and b.start_at <= t.ev_time
         and (b.end_at is null or b.end_at >= t.ev_time)) > 1) as multi_window_conflict,
  count(*) filter (where company_id is not null and ev_time is not null and (
      select count(*) from device_bindings b
       where b.device_id=t.device_id and b.greenhouse_id=t.greenhouse_id
         and b.start_at <= t.ev_time
         and (b.end_at is null or b.end_at >= t.ev_time)) = 0) as no_window
  from t;

\echo == 8. 跨公司风险：NULL 行的 company 与棚室所属公司是否一致 ==
select count(*) as null_rows,
       count(*) filter (where g.id is null) as greenhouse_missing,
       count(*) filter (where g.company_id is distinct from t.company_id) as company_mismatch
  from telemetry t left join greenhouses g on g.id = t.greenhouse_id
 where t.attribution_status is null;

\echo == 9. 抽样：每个分组 2 条（人工核对用） ==
select device_id, greenhouse_id, company_id, id, "timestamp", sampled_at, time_quality, msg_id, created_at
  from (select t.*, row_number() over (
          partition by device_id, greenhouse_id, company_id order by created_at) rn
          from telemetry t where attribution_status is null) s
 where rn <= 2 order by device_id, created_at;

\echo == 10. 预期改动量（迁移将写入 attribution_migrations） ==
with t as (
  select *, coalesce(sampled_at, "timestamp") as ev_time
    from telemetry where attribution_status is null
), w as (
  select t.*, (select count(*) from device_bindings b
                where b.device_id=t.device_id and b.greenhouse_id=t.greenhouse_id
                  and b.start_at <= t.ev_time
                  and (b.end_at is null or b.end_at >= t.ev_time)) as win_cnt
    from t
)
select 'resolved/backfill_sample_time' as outcome, count(*) as rows
  from w where company_id is not null and ev_time is not null and win_cnt = 1 and sampled_at is not null
union all
select 'resolved/backfill_receive_time_estimated', count(*)
  from w where company_id is not null and ev_time is not null and win_cnt = 1 and sampled_at is null
union all
select 'pending/conflict_binding_window', count(*)
  from w where company_id is not null and ev_time is not null and win_cnt > 1
union all
select 'pending/legacy_time_missing', count(*)
  from w where ev_time is null
union all
select 'pending/legacy_before_attribution', count(*)
  from w where ev_time is not null and company_id is not null and win_cnt = 0
union all
select 'pending/no_company', count(*)
  from w where company_id is null;

\echo == 11. 推定行的边界风险（距窗口起点/终点的距离分布） ==
-- 距起点越近，越可能是"绑定前采集、绑定后补传"被错认成"绑定后采集"
with t as (
  select *, coalesce(sampled_at, "timestamp") as ev_time
    from telemetry where attribution_status is null and sampled_at is null
), m as (
  select t.id,
         min(extract(epoch from (t.ev_time - b.start_at))) as d_start_s,
         min(extract(epoch from (coalesce(b.end_at, now()) - t.ev_time))) as d_end_s
    from t join device_bindings b
      on b.device_id=t.device_id and b.greenhouse_id=t.greenhouse_id
     and b.start_at <= t.ev_time
     and (b.end_at is null or b.end_at >= t.ev_time)
   group by t.id
)
select count(*) as estimated_rows,
       count(*) filter (where d_start_s < 60)  as within_60s_of_bind_start,
       count(*) filter (where d_start_s < 300) as within_300s_of_bind_start,
       min(d_start_s)::numeric(12,1) as min_d_start_s,
       min(d_end_s)::numeric(12,1)   as min_d_end_s
  from m;
