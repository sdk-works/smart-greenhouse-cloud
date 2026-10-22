# 迁移 004（历史遥测归属回填）执行与回滚手册

> 状态：**未在生产执行**。本文只说明"怎么执行、怎么回滚、在哪儿能核对"。
> 执行前置：必须由人确认 `attribution_status` 为 `NULL` 的行如何处理（见文末待决策）。

## 1. 这套脚本改的是哪些数据

只动 `telemetry` 表里 `attribution_status IS NULL` 的行（生产当前 7,053 行），
以及新增一张记录表 `attribution_migrations`。**不动**已经 `resolved` 的 11,443 行。

每行落三个字段：`attribution_status`（resolved / pending）、`attribution_reason`、`binding_id`、`binding_version`。

## 2. 执行顺序（生产必须先 up 再切代码）

```
# 0) 备份（生产库，由值班执行，夜间代理不执行）
/opt/greenhouse/scripts/backup_db.sh

# 1) 只读前置核对（可在生产直接跑，不改任何东西）
psql -d greenhouse -f migrations/004_preflight.sql
psql -d greenhouse -f migrations/004_preflight_sampled_time_sanity.sql

# 2) 在一个事务里回填（004_data_reliability_up.sql 自带 BEGIN/COMMIT）
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/004_data_reliability_up.sql

# 3) 复核：记录表行数应等于受影响行数
psql -d greenhouse -c "select code, rows_affected, executed_at from attribution_migrations order by id"
```

004 必须早于 005（业务模块），两者都早于候选代码上线。

## 3. 回滚

```sql
-- 只回退本迁移标记过的行：有 backfill_* reason 的恢复成 NULL，其它行不动
UPDATE telemetry SET attribution_status=NULL, attribution_reason=NULL,
                     binding_id=NULL, binding_version=NULL
 WHERE attribution_reason IN ('backfill_sample_time','backfill_receive_time_estimated');

DROP TABLE IF EXISTS attribution_migrations;
ALTER TABLE telemetry DROP COLUMN IF EXISTS binding_version;
ALTER TABLE telemetry DROP COLUMN IF EXISTS binding_id;
ALTER TABLE telemetry DROP COLUMN IF EXISTS attribution_reason;
```

已封装：`migrations/004_data_reliability_down.sql`（同一个事务）。

注意：down 脚本会连带删掉 `alerts` / `incidents` / `incident_events` 三张表。
如果 004 上线后这些表已经开始写入业务数据，回滚只剔归因字段，不要整段跑 down。

## 4. 2026-10-22 夜间的实测前置结果（生产只读，未执行迁移）

| 口径 | 行数 | 依据 |
|---|---|---|
| `attribution_status IS NULL` | 7,053 | 全部属 1 个（公司, 棚室, 设备）组合，无缺维 |
| `resolved/backfill_sample_time`（可信） | **4** | `sampled_at` 存在且与服务器接收时刻相差 −0.6s |
| `resolved/backfill_receive_time_estimated`（推定） | **7,047** | 只有接收时刻；与 `created_at` 相差 1~4ms |
| `pending/legacy_before_attribution` | **2** | `JY001/99/1`、`JY001/99/2` |
| pending 冲突窗口 / 缺公司 / 跨公司不一致 | 0 | — |
| 已执行过（记录表 + 新字段） | **无** | `attribution_migrations` 不存在，新字段 0 个 |

三个归属分支的数量如上表。

## 5. 推定行的边界风险（§4 里 7,047 那一级）

- 距绑定窗口起点 `< 60s` 的行：**1** 条（最近的一条距起点 26.8s）。
  即"绑定刚生效那一分钟内上传的数据"，有可能是绑定前采集、绑定后补传。
- 距终点最小 135,862.2s（绑定至今未结束），终点方向无风险。

## 6. 必须先看的一件事：`time_quality=1` 不等于时钟可信

2026-10-22 夜间实测：同一台设备 JY001 的 `JY001/99/1`、`JY001/99/2` 两条，
`time_quality` 同为 **1**、`sampled_at` 却是 **2025-10-17 11:00:00**（比真实接收时刻早整整一年）。

也就是说"设备自称有时钟"和"时钟是对的"是两回事。若迁移只看 `sampled_at 是否存在`，
一旦绑定期限再往前挪，这类行就会被判成"可信"并写死 `resolved`。

`004_preflight_sampled_time_sanity.sql` 给出偏差分布与超阈值明细（当前：4 条 ≤0.6s，2 条 ≈ −31,535,000s）。
建议：执行前先在迁移里加护栏——`|sampled_at − 接收时刻| > 300s` 的行降级为
`pending/legacy_sampled_time_implausible`，**不要**拿接收时间兜底。
护栏加上后预计仍为 4 / 7047 / 2，只是这 2 条的理由从"早于绑定"变成"采集时刻不可信"。

## 7. 待决策

1. 7,047 行"推定"是否允许直接写 `resolved`（页面会当成可信数据展示）？
   建议：落 `resolved` 但保留 `attribution_reason`，状态页对这个 reason 显示"推定"角标（T4 已实现）。
2. `attribution_status` 为 `NULL` 的历史行是否一律回填？（现有 7,053 行，全部集中在 48 小时内）
3. 是否接受 §6 的偏差护栏？
