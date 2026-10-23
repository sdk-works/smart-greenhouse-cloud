# 悬挂 job 清理工具 · 2026-10-23

## 0. 先说结论：**本机生产库里没有这类 job 表**

任务书说"生产已知 7 个历史悬挂（10-17~10-20）：5 条 turn 已 completed 但 judge=queued，
2 条 turn 不存在"。**我在本机找不到这套数据**，证据如下。

本机全部数据库：

```
attrib_mig_test  attrib_time_test  greenhouse  greenhouse_migrate_test
greenhouse_nightly_test  jingyang_web_db  observe_test  postgres  template0  template1
```

`greenhouse`（生产库）全部 28 张表与大致行数（`pg_stat_user_tables`，只读）：

```
event_standard_log 19323   telemetry 18993   device_receipts 18893   raw_messages 11994
greenhouses 2    mqtt_credentials 1   bases 1   device_bindings 1   inspection_points 1
devices 1    companies 1   users 1
robot_point_config_releases 0   deployments 0   device_logs 0   inspection_visits 0
actuator_profiles 0   nav_point_discoveries 0   robot_tasks 0   robot_point_configs 0
audit_logs 0    control_rules 0   robot_point_mappings 0   actuator_commands 0
map_versions 0  point_mappings 0  inspection_batches 0   images 0
```

- 没有任何 `job` / `turn` / `judge` / `queue` 表；
- 代码里 `grep -rn "judge" app/` 与 `grep -rn "\bturn\b" app/` **零命中**；
- Redis 生产库（`6379/0`）`dbsize = 0`，db1~db5、db15 也都是 0 —— 队列也不在 Redis 里；
- 另一套 `jingyang_web_db` 里有 `translation_jobs`（状态含 pending/processing/success/failed/waiting_config），
  但 **0 行**，且与 turn/judge 无关。

盘点的实际执行（表不存在时工具明确报错，不静默返回 0）：

```
$ python3 scripts/stuck_job_sweeper.py --db-url "$DATABASE_SYNC_URL" \
    --table job_judges --ref-table job_turns --dry-run --json
{"error": "表 job_judges 不存在（库=greenhouse）——无法盘点，请确认表名与所在库"}
exit=4
```

**判断**：这 7 条悬挂属于 **141 内网智农AI 的 transport / judge 链路**（任务书已明确它
"不在本机、不在本任务池"）。本机无法盘点、也无法清理。**工具已按通用口径写好**，
拿到那套库的表名/列名后一条命令即可跑（见 §4）。

## 1. 交付物

| 文件 | 作用 |
|---|---|
| `scripts/stuck_job_sweeper.py` | 通用悬挂 job 识别 + 清理（表/列全部可配） |
| `tests/test_stuck_job_sweeper.py` | 测试库 6 条 |
| 本文 | 口径、演练结果、本机盘点结论、"会不会再有"的分析 |

## 2. 识别口径（三条，任一命中即判悬挂）

设 job 表为下游（judge），上游表为 turn：

| # | 口径 | 判据 | 为什么是悬挂 |
|---|---|---|---|
| 1 | `orphan` | job 的外键为 NULL，或在上级表里**找不到**对应记录 | 上游都没了，下游永远不会有人来推进 |
| 2 | `terminal_conflict` | 上游状态 ∈ `completed/success/done`，而 job 仍是 `queued/pending` | 上游都完成了，下游还没被拉起 —— 拉起动作**已经错过**，之后不会再触发 |
| 3 | `timeout` | job 状态 ∈ `running/processing` 且 `updated_at` 早于 `now - 阈值`（默认 3600s） | 卡在中途，既不前进也不失败 |

**关键的"不误伤"约束**：三条口径都额外要求
`job.status ∈ (pending 集合 + running 集合)` —— 已经 `failed/success` 的历史记录不算悬挂。
并且 **`terminal_conflict` 只在"上游已进终态"时成立**：上游还在跑或还没开始的
`queued` 是**正常排队**，工具一条都不碰（有专门用例钉住）。

## 3. 测试库演练

```
$ /opt/greenhouse-nightly/run_tests.sh tests/test_stuck_job_sweeper.py -q
......                                                                   [100%]
6 passed
```

造的数据（`sweep_turns` / `sweep_judges`，跑完全表删除）：

| job | turn | job 状态 | 期望 | 实测 |
|---|---|---|---|---|
| `J-CONFLICT-1` | `T-DONE-1` completed | queued | terminal_conflict | ✓ |
| `J-CONFLICT-2` | `T-DONE-2` completed | queued | terminal_conflict | ✓ |
| `J-ORPHAN-1` | `T-GONE-1` **不存在** | queued | orphan | ✓ |
| `J-ORPHAN-2` | NULL | queued | orphan | ✓ |
| `J-TIMEOUT-1` | `T-RUN` running | running，3 小时未更新 | timeout | ✓ |
| `J-NORMAL-1` | `T-RUN` running | queued | **不动** | ✓ 未命中 |
| `J-NORMAL-2` | `T-NEW` pending | queued | **不动** | ✓ 未命中 |
| `J-FRESH-RUN` | `T-RUN` running | running，30 秒前更新 | **不动** | ✓ 未命中 |

用例清单：

| 用例 | 断言 |
|---|---|
| `test_detects_three_kinds_and_spares_normal_queued` | 5 条命中、3 条正常的一条都不碰 |
| `test_dry_run_does_not_write` | dry-run 后再查仍是 5 条（没写库） |
| `test_apply_converges` | `--apply` 清 5 条 → 再 dry-run **命中 0**（收敛）；正常两条状态与 `last_error` 均未变；清理的 5 条变 `failed` 且 `last_error` 以 `[sweeper] ` 开头写明原因 |
| `test_limit_caps_scope` | `--limit 2` 只清 2 条 |
| `test_missing_table_reports_instead_of_crashing` | 表不存在 → 退出码 4 + 明确报错（**不**静默返回"0 条悬挂"） |
| `test_refuses_to_write_non_test_database` | `--apply` 指向 `greenhouse` 库 → 退出码 3 |

## 4. 拿到真实表结构后怎么用

```bash
# 1) 先只读盘点（任何库都能跑，不会写）
python3 scripts/stuck_job_sweeper.py \
  --db-url "<DSN>" \
  --table job_judges --id-col id --status-col status \
  --ref-col turn_id --ref-table job_turns --ref-id-col id --ref-status-col status \
  --pending-statuses "queued,pending" \
  --running-statuses "running,processing" \
  --terminal-statuses "completed,success,done" \
  --time-col updated_at --timeout-seconds 3600 \
  --dry-run

# 2) 确认无误后清理（--apply 只对库名含 greenhouse_nightly_test 的库生效；
#    生产库要清必须由人显式放行，见下）
python3 scripts/stuck_job_sweeper.py ... --apply --set-status failed --reason-col last_error
```

清成什么状态由 `--set-status` 决定（默认 `failed`，不改成就当"没做过"）。
原因会写进 `--reason-col`（如 `last_error`），格式 `[sweeper] <口径>: <中文说明>`，可逐条复核。

**注意**：工具的 `--apply` 硬校验是"库名必须含 `greenhouse_nightly_test`"。
要在智农AI 那套库上执行，需要人先确认库名/权限，再决定是否放宽这条校验 —— **本次没有放宽**。

## 5. "清完之后还会不会再有"（针对 5 条终态矛盾 + 2 条孤儿）

这两类各有各的根因，**只清数据不修机制，一定会再生**：

| 类别 | 最可能的机制缺陷 | 需要确认什么 | 建议 |
|---|---|---|---|
| **终态矛盾**（上游 completed、下游仍 queued） | 下游 job 靠**轮询**被拉起：上游完成的那一刻没被扫到，之后轮询条件里没有"上游已完成但下游未启动"这一路，于是永久漏掉 | 下游是被"上游完成时发消息"拉起，还是被定时扫描拉起？ | ① 拉起改成消息 + 兜底补扫；② 把 sweeper 挂成定时任务（`terminal_conflict` 口径），每天扫一次 |
| **孤儿**（turn 记录不存在） | 上游记录被**删掉或没落库**，下游先建了；或两套存储之间不是同一事务 | turn 是被删除了，还是从来没写进去？后者更严重（说明有丢写） | ① 先查 2 条的 turn_id 在不在别的历史表/备份里；② 上下游用一个事务写；③ 加外键约束（现在没有） |

**本次无法验证上述任一条**（数据不在本机）。拿到库后第一步应该查：

```sql
-- 那 5 条：上游完成时刻 vs 下游 job 创建时刻，看是不是"同一秒"（同事务）还是"差很多"
-- 那 2 条：turn_id 是否在备份/历史表里能找到
```

## 6. 边界

- 工具只做 **`UPDATE` 状态 + 写原因**，**不删任何行**。
- `--dry-run` 是默认；`--apply` 必须显式加。
- 本次**没有**在生产库执行任何 `--apply`；生产库上这张表根本不存在，盘点结果为"无法盘点"。
