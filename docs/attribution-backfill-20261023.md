# 004 归属回填：可执行脚本 + 预演结论 · 2026-10-23

> 生产**只做只读预演**，一行都没写。脚本自带第二道闸：`--apply` 遇到库名不含
> `greenhouse_nightly_test` 直接退出码 3 拒绝执行。

## 1. 交付物

| 文件 | 作用 |
|---|---|
| `scripts/backfill_attribution.py` | 分级 + 预演 + 限量 + 只写可信 + 实写 + 生成回滚 SQL |
| `tests/test_backfill_attribution.py` | 测试库 6 条（七种场景 + 五种用法） |
| 本文 | 生产只读数字、抽样证据、回滚口径、与现行迁移的差异 |

## 2. 分级口径（护栏优先）

按判定顺序，命中即返回：

| # | 条件 | 结果 | 性质 |
|---|---|---|---|
| 1 | `company_id IS NULL` | `pending / no_company` | 缺维，不猜 |
| 2 | `coalesce(sampled_at, timestamp) IS NULL` | `pending / legacy_time_missing` | 无时间证据 |
| 3 | **`|sampled_at - timestamp| > 300s`** | **`pending / sample_time_implausible`** | **护栏（本次新增）** |
| 4 | 覆盖窗口 > 1 | `pending / conflict_binding_window` | 报冲突，不任取一条 |
| 5 | 覆盖窗口 = 0 | `pending / legacy_before_attribution` | 绑定之前的历史 |
| 6 | 恰好 1 个窗口 + 有 `sampled_at` | `resolved / backfill_sample_time` | **可信** |
| 7 | 恰好 1 个窗口 + 只有接收时间 | `resolved / backfill_receive_time_estimated` | **推定** |

护栏放在窗口判定**之前**：时钟错乱的行即便"看起来落在窗口内"也不能进可信。
阈值 `--max-skew`，默认 300s，可调。

> 注：`telemetry.timestamp` 是**服务器接收时刻**（不是采集时刻），已在
> `004_data_reliability_up.sql` 的注释里论证过（`timestamp` 与 `created_at` 相差 1~4ms）。
> 所以"推定"档的真实含义是"用收到报文的时刻代替采集时刻"，粒度是设备联网的那一刻。

## 3. 生产只读预演（实跑）

```
$ python3 scripts/backfill_attribution.py --db-url "$DATABASE_SYNC_URL" --dry-run --sample 3
========================================================================
004 归属回填（DRY-RUN，未写入）
  库=greenhouse  最大允许偏差=300s  limit=不限  only-trusted=否
  范围内行数 = 7053
========================================================================
status     reason                                   行数
------------------------------------------------------------------------
resolved   backfill_receive_time_estimated        7047
resolved   backfill_sample_time                      4
pending    sample_time_implausible                   2
------------------------------------------------------------------------

抽样（前 3 条）：
  'JY001/88/1'  recv=2026-10-17 10:28:52.557026+08 sampled=2026-10-17 10:28:52+08 tq=1 skew=-0.6s -> resolved/backfill_sample_time
  'JY001/88/2'  recv=2026-10-17 10:28:56.561531+08 sampled=2026-10-17 10:28:56+08 tq=1 skew=-0.6s -> resolved/backfill_sample_time
  'JY001/88/3'  recv=2026-10-17 10:29:00.566014+08 sampled=None         tq=0        -> resolved/backfill_receive_time_estimated
```

**与 10-22 基线的差异只有一处**：2 条 pending 的 reason 由 `legacy_before_attribution`
变成 `sample_time_implausible`。行没变（还是那 2 条），只是护栏先命中，理由更准：

```
$ psql -d greenhouse -c "select msg_id, \"timestamp\" as recv, sampled_at, time_quality,
    round(extract(epoch from (sampled_at - \"timestamp\"))/86400)::int as skew_days
  from telemetry where attribution_status is null and sampled_at is not null
    and abs(extract(epoch from (sampled_at - \"timestamp\"))) > 300;"
   msg_id   |             recv              |       sampled_at       | time_quality | skew_days
------------+-------------------------------+------------------------+--------------+-----------
 JY001/99/1 | 2026-10-17 10:48:24.862459+08 | 2025-10-17 11:00:00+08 |            1 |      -365
 JY001/99/2 | 2026-10-17 10:50:35.299119+08 | 2025-10-17 11:00:00+08 |            1 |      -365
```

即 **`time_quality=1` 只能说明设备自称有时钟，不能证明时钟正确**（偏差整整一年）。
可信的 4 条偏差为 **−0.6s**。

`--only-trusted` 在生产上（只读）：

```
$ python3 scripts/backfill_attribution.py --db-url "$DATABASE_SYNC_URL" --dry-run --only-trusted --limit 10
  库=greenhouse  最大允许偏差=300s  limit=10  only-trusted=是
  范围内行数 = 4
resolved   backfill_sample_time                      4
```

## 4. 测试库演练（与生产同构的样本）

```
$ /opt/greenhouse-nightly/run_tests.sh tests/test_backfill_attribution.py -v
tests/test_backfill_attribution.py ......                                [100%]
============================== 6 passed in 1.51s ===============================
```

| 用例 | 覆盖 |
|---|---|
| `test_classification_matches_expected` | 七种场景一次性对齐：窗口内可信 / 窗口内推定 / 时钟错一年（护栏）/ 早于窗口起点 / 无 company / 双窗口冲突 / 无绑定 |
| `test_guard_is_tunable` | 阈值调到 400 天后"错一年"那行被放行 —— 证明确实是护栏在起作用，不是"窗口没覆盖"的假象 |
| `test_only_trusted_and_limit` | `--only-trusted` 只剩可信档；`--limit 1` 范围内只有 1 行 |
| `test_apply_writes_and_rollback_restores` | `--apply` 写 3 行 → 断言三档各自落库 → 执行生成的回滚 SQL → 断言全部恢复成 NULL（含 `binding_id`） |
| `test_only_trusted_leaves_estimated_untouched` | `--apply --only-trusted` 时**推定档一行都不动** |
| `test_refuses_to_write_non_test_database` | `--apply` 指向 `greenhouse` 库 → 退出码 3、stderr 含"拒绝写库" |

## 5. 回滚

`--apply` 会落盘两份文件（默认 `/opt/greenhouse-nightly/logs/`）：

- `backfill-attribution-<ts>.json`：写入的 id 清单 + 分级汇总
- `backfill-attribution-<ts>.rollback.sql`：可直接执行的回滚

被处理的行在改动前 `attribution_status` **一律是 NULL**（脚本只处理 NULL 行），
所以回滚就是把这批 id 恢复成 NULL：

```sql
BEGIN;

UPDATE telemetry
   SET attribution_status = NULL,
       attribution_reason = NULL,
       binding_id         = NULL,
       binding_version    = NULL
 WHERE id IN ('<id1>', '<id2>', ...);

-- 校验：下面这条应为 0
SELECT count(*) AS still_marked FROM telemetry
 WHERE id IN ('<id1>', '<id2>', ...)
   AND attribution_status IS NOT NULL;

COMMIT;
```

若已无法拿到 manifest（比如文件丢了），**不要用** `WHERE attribution_reason IN (...)`
这类条件回滚——那会把之后正常写入的行一起清掉。这种情况下只能按
`attribution_migrations` 里记录的批次时间窗 + `created_at` 范围人工核对。

## 6. 与现行迁移的差异（**上线前必须处理**）

`migrations/004_data_reliability_up.sql` **还没有这条护栏**：它把"有 `sampled_at`"
的行一律判成 `resolved/backfill_sample_time`。若直接执行该迁移，那 2 条时钟错一年的行
会因为"落在窗口外"而落到 `legacy_before_attribution`——**结论碰巧也是 pending**，
但那是靠"窗口没覆盖"碰上的，不是靠识别出来时钟不可信。

要在迁移里补上护栏，把 1a) 的 `cand` CTE 加一行过滤即可：

```sql
   WHERE t.attribution_status IS NULL
     AND t.company_id IS NOT NULL
     -- ↓ 新增：采集时间与接收时刻偏差 > 300s 的一律不进可信，留给 1c) 判 pending
     AND NOT (t.sampled_at IS NOT NULL
              AND abs(extract(epoch from (t.sampled_at - t."timestamp"))) > 300)
```

并在 1c) 的 `CASE` 里加分支
`WHEN abs(extract(epoch from (sampled_at - "timestamp"))) > 300 THEN 'sample_time_implausible'`。

**本次没有改这个迁移文件**（它已评审过，改口径属于需要人拍板的范围，见 §7）。
推荐的执行路径是：**用脚本跑**（脚本已内置护栏，且带 manifest 与回滚）。

## 7. 待决策

1. **7,047 行"推定"要不要直接写 `resolved`？**
   影响：写 `resolved` 后页面会当可信展示（T4 状态接口已按 reason 分档显示"推定"角标，未上生产）。
   建议：写 `resolved` 但保留 reason，展示层分档。
2. **护栏阈值 300s 是否接受？** 建议接受（实测有 2 行差一年）。
3. **走脚本还是走迁移？** 建议走脚本（有护栏 + manifest + 回滚）；若坚持走迁移，先补 §6 那段。
4. **执行时机**：需要停机窗口吗？纯 UPDATE 7,053 行不需要停机，但会持行锁；
   建议放在夜间低峰，且**执行前先 `backup_db.sh`**。

## 8. 复现

```bash
# 生产只读预演（不写）
python3 scripts/backfill_attribution.py --db-url "$DATABASE_SYNC_URL" --dry-run --sample 5

# 测试库
/opt/greenhouse-nightly/run_tests.sh tests/test_backfill_attribution.py -v
python3 scripts/backfill_attribution.py --dry-run                 # 默认连 DATABASE_SYNC_URL
python3 scripts/backfill_attribution.py --apply --only-trusted    # 只写可信
python3 scripts/backfill_attribution.py --apply --limit 100       # 先小批量
```
