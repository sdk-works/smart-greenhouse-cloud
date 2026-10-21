# 验收手册（acceptance）

> 对象：M1 隔离收尾 / M2 机器人闭环 / M3 数据可靠性 / M4 业务模块 / I 集成联调
> 环境：**全部在测试库 `greenhouse_nightly_test` 与 worktree 内完成，
> 生产环境未被触碰**（未 restart、未 push、未改 .env / nginx / EMQX、未写生产库）。

## 0. 状态标注口径（逐项只能选一个）

| 标注 | 含义 |
|---|---|
| 未完成 | 代码未写完 / 未提交 |
| 代码完成 | 已提交，但测试未跑或未通过 |
| 自动测试通过 | 已实际执行 pytest 并通过（需写明用例数与文件） |
| 模拟联调通过 | 用模拟数据/模拟设备跑通链路（**不得写成真实环境**） |
| 真实环境验证通过 | 真实设备/真实环境实测（本次基本没有，只读查询除外） |

## 1. 准备

```bash
cd /opt/greenhouse-nightly/worktree
# 切到要验收的分支
git checkout night/business          # 或 night/iso-finish / robot-loop / data-reliability / integration
# 迁移只在测试库执行
/opt/greenhouse-nightly/testdb_sql.sh migrations/003_robot_loop_up.sql
/opt/greenhouse-nightly/testdb_sql.sh migrations/004_data_reliability_up.sql
/opt/greenhouse-nightly/testdb_sql.sh migrations/005_business_up.sql
```

## 2. 跑测试（唯一认可的方式）

```bash
cd /opt/greenhouse-nightly/worktree
/opt/greenhouse/venv/bin/python -m pytest -q -p no:cacheprovider
```

基线 67 个；本夜结束时应为 **127 个全部通过**。
测试夹具会在开跑前自动把 003/004/005 应用到测试库（见 `tests/conftest.py`），
因此上面那三步手工迁移是为了演示"迁移本身可执行"，不是测试的前置条件。

分模块跑：

```bash
/opt/greenhouse/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_m1_isolation.py
/opt/greenhouse/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_m2_robot_loop.py
/opt/greenhouse/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_m3_data_reliability.py
/opt/greenhouse/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_m4_business.py
/opt/greenhouse/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_integration_loop.py
```

## 3. 逐项演示步骤

| # | 验收项 | 操作 | 预期 |
|---|---|---|---|
| 1 | 仪表盘按公司聚合 | 以 A 公司用户 `GET /api/v1/dashboard/stats`，记录各项；给 A 新增一套数据后再查 | A 的 6 项计数各 +1，B 公司数据不受影响 |
| 2 | 跨公司审计 | 平台运营 `GET /api/v1/dashboard/stats?company_id=<B>` | 200；`audit_logs` 多一条 `cross_company_read` |
| 3 | 业务用户越权 | 业务用户同上带别家 `company_id` | 403 |
| 4 | 缓存键隔离 | 见 `test_cache_keys_are_company_prefixed` | 键形如 `c:<company_id>:device:online:<dev>` |
| 5 | 照片归档 | `GET /api/v1/photo-catalog?greenhouse_id=&page=1&page_size=2` | `total/page/page_size` + items 字段齐全 |
| 6 | 地图版本不可变 | 发布 v1 → 改点位 → 发布 v2 → 读 v1 | v1 快照与发布时一致 |
| 7 | 任务快照 | 创建任务 → 编辑任务 | `snapshot` 不变 |
| 8 | 任务状态机 | `POST /tasks/{id}/status {"status":"completed"}`（当前 pending） | 400 非法流转 |
| 9 | 地图版本核对 | 登记 map-v1 → 回传 map-v2 | 落 open 告警，登记值仍是 map-v1 |
| 10 | 补传归属 | 设备 2023 绑 A、之后绑 B；补传 2023 的数据 | 归属 A，status=resolved；无绑定历史 → pending |
| 11 | 新鲜度 | `GET /api/v1/freshness/devices` | 返回 age/basis/state，改数据时间后即时变化 |
| 12 | 重启恢复 | `POST /api/v1/maintenance/recover` | 超时 running 的任务/批次 → partial |
| 13 | 事件与告警 | 建事件 → 建告警 → 清告警 | 告警 cleared，事件仍是 pending_confirm |
| 14 | 绑定强制 | 同通道重叠再绑 / 版本回退 | 均 409 |
| 15 | 控制命令 | 建命令 → send → ack → result(executing) → result(executed) | ack 时 `is_executed=false`，executed 时 true |
| 16 | AI 受控 | `POST /api/v1/ai/advice` | `limits.db_access=false`、`mqtt_control=false`，产出无 control |
| 17 | 报表导出 | `GET /api/v1/reports/telemetry?fmt=csv` | 只含本公司数据 |
| 18 | 报文清理 | `python scripts/purge_raw_messages.py` | dry-run 不删；指向生产库直接拒绝（exit 2） |

## 4. 禁止的验收动作（会破坏隔离）

- ❌ 对生产库执行任何迁移/DELETE/UPDATE（用 `dbq.sh` 只读）。
- ❌ `systemctl restart greenhouse` / 改 `.env` / 改 nginx / 改 EMQX。
- ❌ `git push`、向 master 合并。
- ❌ 向真实设备下发控制指令。

## 5. 迁移与回滚

| 迁移 | 内容 | 回滚 |
|---|---|---|
| `003_robot_loop_{up,down}.sql` | 照片归档列、inspection_evidence、robot_tasks.snapshot、状态列去枚举、robot_map_registry / robot_map_alerts | `003_robot_loop_down.sql` |
| `004_data_reliability_{up,down}.sql` | telemetry 归属留痕列、incidents / incident_events / alerts、绑定索引 | `004_data_reliability_down.sql` |
| `005_business_{up,down}.sql` | planting_batches、metric_definitions（含最小集种子）、ai_advice_logs | `005_business_down.sql` |

**执行范围：仅在 `greenhouse_nightly_test` 执行过（用 `testdb_sql.sh`，
该脚本硬编码连测试库）。生产库一次都没跑。**

注意：003 会把 `robot_tasks.status` 从 PG 原生枚举 `taskstatus` 转成 `VARCHAR(20)`，
回滚脚本**不会**自动转回枚举（见 003_down 里的注释）——因为那样会丢掉
`paused`/`partial` 状态对应的数据。
