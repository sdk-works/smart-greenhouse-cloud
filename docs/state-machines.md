# 状态机总表（state-machines）

> 2026-10-21 夜间固化。所有流转都在**代码层强制**，非法流转一律 `400`。
> 数据库里状态列均为普通字符串（不是 PG 枚举），新增状态不需要改列类型。

## 1. 巡检任务（robot_tasks.status）

定义：`app/core/task_state.py`　对外查询：`GET /api/v1/tasks/task-state-machine`

| 当前 | 可达 |
|---|---|
| `pending` 待执行 | `running` / `cancelled` / `failed` |
| `running` 执行中 | `paused` / `completed` / `partial` / `failed` / `cancelled` |
| `paused` 已暂停 | `running` / `completed` / `partial` / `cancelled` |
| `completed` 已完成 | ——（终态） |
| `partial` 部分完成 | ——（终态） |
| `failed` 失败 | ——（终态） |
| `cancelled` 已取消 | ——（终态） |

接入点：`POST /tasks/{id}/status`、`PUT /tasks/{id}`、`POST /tasks/{id}/dispatch`
（→ running）、`POST /tasks/{id}/cancel`（→ cancelled）。

兜底：M3 的 `POST /maintenance/recover` 与启动钩子把超时 `running` 标记为 `partial`，
并在 `error_message` 写明"实际执行结果未知"。

## 2. 异常事件单（incidents.status）

定义：`app/core/incident_state.py`　对外查询：`GET /api/v1/incidents/state-machine`

| 当前 | 可达 |
|---|---|
| `pending_confirm` 待确认 | `confirmed` / `closed` |
| `confirmed` 已确认 | `handling` / `closed` |
| `handling` 处理中 | `pending_review` / `confirmed` / `closed` |
| `pending_review` 待复核 | `closed` / `handling` |
| `closed` 已关闭 | ——（终态） |

每次流转都写 `incident_events`（谁、何时、从什么状态到什么状态）。

**关键：** 告警停止 ≠ 事件关闭。
`POST /alerts/{id}/clear` 默认只置 `alerts.cleared_at`，事件单状态不变；
必须显式带 `?auto_close_incident=true` 才会同时关闭事件单。

## 3. 执行器命令（actuator_commands.status）

定义：`app/core/command_state.py`　对外查询：`GET /api/v1/actuator-commands/state-machine`

| 当前 | 可达 |
|---|---|
| `pending` 待发送 | `sent` / `cancelled` |
| `sent` 已发送 | `accepted` / `rejected` / `timeout` / `cancelled` |
| `accepted` 已接收（**ACK**） | `executing` / `failed` / `timeout` / `cancelled` |
| `executing` 执行中 | `executed` / `failed` / `timeout` |
| `executed` 成功 | ——（终态） |
| `failed` 失败 | ——（终态） |
| `rejected` 拒收 | ——（终态） |
| `timeout` 超时 | ——（终态） |
| `cancelled` 已取消 | ——（终态） |

**ACK ≠ 执行结果**：只有 `executed` 算成功。
接口返回的 `is_acked` / `is_executed` / `is_finished` 必须分开看：
`accepted` 时 `is_acked=true` 但 `is_executed=false`。

## 4. 种植批次（planting_batches.status）

定义：`app/api/planting_batches.py`

| 当前 | 可达 |
|---|---|
| `planned` 计划中 | `active` / `closed` |
| `active` 在种 | `harvested` / `closed` |
| `harvested` 已采收 | `closed` |
| `closed` 已归档 | ——（终态） |

## 5. 巡检批次（inspection_batches.status）

沿用既有实现：`running` 在 `IDLE_TIMEOUT_MINUTES=15` 无新事件后标记为 `partial`
（`close_reason=arrival_event_timeout`）；M3 的恢复钩子用
`close_reason=stale_running_recovery`。

## 6. 通用约定

- 所有终态都**不可再流转**。
- 目标状态与当前相同时视为幂等，放行。
- 未知状态一律 `400`，不会静默落库。
