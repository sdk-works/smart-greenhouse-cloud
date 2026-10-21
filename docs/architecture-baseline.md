# 智慧温室平台 · 架构基线（2026-10-21 第一版）

> 本文件把你提出的框架固化为可执行的基线，并映射到当前代码。原则：**先固定对象关系、数据契约、接口边界、状态流转和权限规则，再扩展页面。**

## 一、今日结论：大棚管理"进不去"已修复

**根因**（不是后端缺接口，而是契约错位）：前端 `pages/GreenhouseWorkbench.js` 从 `GET /api/v1/workbench/greenhouses/{id}` 读取 `data.profile`、`data.map`、`data.latest`，但昨日 agent 猜写的 `workbench.py` 返回 `{id,name,map_data,points,devices,summary}`。前端拿不到 `data.map`，执行 `doc.points.length` 时抛 `TypeError`，整页崩溃。**接口是 200，但页面白屏**——所以表现为"进不去"。

**修复**：新建 `app/core/map_doc.py` 作为地图文档唯一读写入口，按前端源码（source map 反解的原始 JS）逐字段重写 `workbench.py` 与 `point_configs.py` 的 maps/map-monitor：

| 接口 | 修复后返回 |
|---|---|
| `GET /workbench/greenhouses/{id}` | `{profile, map:{revision,layout,points,defaults,next_name}, latest}` |
| `POST /workbench/greenhouses` | `{greenhouse_id}`（前端据此跳转） |
| `PUT /workbench/greenhouses/{id}/profile` | `{success, revision}`（带乐观锁） |
| `GET .../points/{pointId}?days=N` | `{point, observations:[{values,quality,captured_at}], total, truncated, position_history, photos, photo_total, visits}` |
| `GET /point-config/maps/{id}` | 完整地图文档（layout/points/defaults/next_name/revision） |
| `PUT /point-config/maps/{id}` | 按 point.id 合并，**保留 binding**，revision+1 |
| `PUT .../points/{pointId}/binding` | 按点位 id 关联/解除，同步 `robot_point_configs` |
| `GET /point-config/map-monitor/{robot}` | 增加 `latest_arrival / point_results / published_points / draft_differs` |

验证：pytest **63 passed**；生产重启后真实数据返回结构完全对齐；旧棚空 `map_data` 自动迁移为默认布局；DTU 正常上数；启动无报错。

## 二、已采纳的架构决策

1. **模块化单体**，不拆微服务；接入/报表等后台任务独立进程，不阻塞 Web。
2. **沿用现有技术栈与数据库**（FastAPI + PostgreSQL + Redis + EMQX）。
3. **稳定 ID 关联**，名称仅用于展示；`greenhouses.id`、`devices.device_id`、`points[].id` 均为稳定键。
4. **大棚编号 ≠ 设备编号**；机器人 ≠ 巡检点；当前归属不覆盖历史归属（见 §四 差距）。
5. **固定 IoT / 机器人 / 人工录入 三类来源**，统一经"接入与标准化"层入库。
6. **青禾 AI 只经受控接口读写**，不直连数据库、不直接发 MQTT。
7. **权限贯穿全链路**：角色控制"能做什么"，所属公司/基地/大棚控制"能操作哪里"。
8. **契约文件与代码同版本管理**（本文件 + openapi）。

## 三、现状模块映射

| 框架模块 | 现有代码 | 状态 |
|---|---|---|
| 身份/权限/接口/审计 | `app/api/auth.py`、`app/api/__init__.py` 全局 `get_current_user` | ✅ 登录+RBAC 已在生产强制 |
| 资产管理 | `greenhouses.py`、`devices.py`、`workbench.py`、`point_configs.py` | ✅ 大棚/设备/点位/绑定 |
| 环境监测 | `telemetry.py`、`workbench.py` 的 `latest`/observations | ✅ 最新值+历史 |
| 巡检管理 | `robot_patrol.py`、`inspection_batches.py`、`tasks.py` | ⚠️ 有任务/批次，状态机未固化 |
| 异常处理 | — | ❌ 无 |
| 设备控制 | `devices.py` send_command、`point_configs.py` publish | ⚠️ 有下发，命令状态机未固化 |
| 数据接入与标准化 | `app/mqtt/mqtt_service.py` | ✅ 采集链路稳定（整夜 0 缺口） |
| 公共支撑 | 备份脚本、Redis、日志 | ✅ 基础具备 |
| AI 分析 | — | ❌ 无 |

## 四、与框架的差距（按优先级）

### P0 —— 影响正确性/安全（建议本周）
1. **公司/基地层级缺失**：当前只有 `greenhouses`，无 `companies`/`bases`，权限无法按公司/基地隔离。`users` 无 `company_id`。
2. **设备绑定无历史**：`devices.greenhouse_id` 是当前值，换棚后历史归属丢失；需 `device_bindings(device_id, greenhouse_id, start_at, end_at)`。
3. **地图无版本**：`greenhouses.map_data` 单份 JSON，地图更新后历史照片/点位无法回溯到原版本；需 `map_versions`。
4. **异常事件缺失**：无 `alerts`/`incidents` 对象，无法承载"待确认→处理中→关闭"。

### P1 —— 结构完整性（两周内）
5. **状态机未固化**：巡检/控制/异常的状态字段散落，需统一枚举与流转校验（见 §六）。
6. **数据契约未成文**：`telemetry` 字段、单位、`valid` 数组顺序、时间语义需落为 `telemetry-v1` 正式契约并加测试。
7. **原始消息表缺失**：当前直接入 `telemetry`；缺 `raw_messages`（原始报文、msg_id、接收时间、解析状态），无法审计与重放。
8. **适配器层未抽象**：新设备接入仍散落在 `mqtt_service`，需 `adapters/` 按厂家隔离。

### P2 —— 完善（后续）
9. 巡检证据（照片/视频）与 `/photo-catalog` 接口缺失（前端已在调用，当前 404）。
10. 种植批次对象（`planting_batches`）未建。
11. 报表/AI 分析进程。

## 五、数据契约（当前落地版）

### 5.1 地图文档（已实现，`app/core/map_doc.py`）
```json
{
  "revision": 3,
  "layout": {"template":"solar","width":10,"height":80,"calibrated":false,
             "aisle_side":"north","aisle_width":1.5,"bays":4,
             "length_axis":"ew","planting_direction":"ns",
             "collection_lane_count":0,"collection_direction":"ns"},
  "defaults": {"sample_seconds":20,"collect_soil":true,
               "collect_environment":true,"take_photo":true,"enabled":true},
  "next_name": "A3",
  "points": [{
    "id":"uuid","point_name":"A1","zone":"中区","x":10.0,"y":20.0,"note":"",
    "sample_seconds":20,"collect_soil":true,"collect_environment":true,
    "take_photo":true,"enabled":true,
    "binding": {"robot_id":"ROB001","nav_point_code":"7","mapping_id":"<cfg id>"}
  }]
}
```
- 存储：`greenhouses.map_data(JSON)`，不新增列。
- 兼容：旧 `{width,height,waypoints[]}` 读取时自动迁移。
- 乐观锁：`revision` 不一致返回 409。

### 5.2 测量数据（telemetry，待正式化）
每条至少表达：公司 / 设备+采集通道 / 指标+数值+单位 / 大棚+位置 / 采样时间+接收时间+时间可信度 / 有效性与是否过期 / 原始消息 ID / （机器人）任务 ID+地图版本+定位质量。
当前 `telemetry` 已有 `sampled_at`、`time_quality`、`msg_id`、`task_id`、`mapping_id`、`chassis_point`、`greenhouse_point`；缺 `company_id`、`map_version`、`raw_message_id`。

## 六、状态机（建议固化）

| 对象 | 状态 |
|---|---|
| 数据质量 | 有效 / 无效 / 过期 / 时间不可信 / 位置不可信 |
| 巡检任务 | 待执行 / 执行中 / 暂停 / 完成 / 部分完成 / 失败 / 取消 |
| 控制命令 | 待发送 / 已发送 / 已接收 / 执行中 / 成功 / 失败 / 超时 / 取消 |
| 异常事件 | 待确认 / 已确认 / 处理中 / 待复核 / 关闭 |

关键约束（写入校验，不只写文档）：
- ACK ≠ 执行结果；"MQTT 发送成功" ≠ "风机已开启"。
- 巡检"走完路线" ≠ "所有点位采集成功"。
- 环境恢复可停持续告警，但事件关闭需人工记录原因。

## 七、权限边界

| 角色 | 能力 |
|---|---|
| 公司管理员 | 本公司全量管理 |
| 基地/大棚负责人 | 所属基地/大棚管理 |
| 操作人员 | 采集/巡检/控制执行 |
| 只读人员 | 查看 |

当前：`auth.PERMISSIONS` 已有细粒度权限矩阵并在生产强制；**缺"公司/基地范围"过滤**（P0-1）。设备身份需限定可上传/订阅/收命令范围，不能仅信任报文中的编号。

## 八、分阶段落地

- **阶段 0（已完成）**：修复大棚管理契约；RBAC 强制；尾斜杠/用户列表泄露修复。
- **阶段 1（下一步）**：`companies`/`bases` 表 + `users.company_id`；`device_bindings` 历史；数据范围过滤；`telemetry` 契约成文 + 测试。
- **阶段 2**：`map_versions`（地图版本化）；`raw_messages`；`alerts/incidents` 状态机；`/photo-catalog`。
- **阶段 3**：适配器层抽象；种植批次；AI 受控工具接口。

## 九、第一条贯穿验收路径（框架 §十）

> 创建公司和大棚 → 绑定 JY001 → 展示八项监测数据 → 建立机器人巡检任务 → 接收一个巡检点的数值与照片 → 在同一座棚查看两种来源 → 创建并处理一条异常。

验收还要覆盖：重复上报不重复入库（已验证）；设备换绑后旧数据归属不变（**待做**）；机器人跨棚历史任务不变（**待做**）；断网补传不冒充实时值（**待做**）；地图更新后历史照片仍能定位（**待做**）；无权限用户不能访问其他公司数据（**待做**）；服务重启后任务不永久卡在执行中（**待做**）。

## 十、今日代码改动审计

| 提交 | 内容 | 验证 |
|---|---|---|
| `b7b72ec` | 新增 workbench/point-config 接口 + 全站 RBAC + 修 `get_current_user` 事务冲突 | 59 tests |
| `e4aeb01` | 尾斜杠 307 消除 + `/auth/users` 补 `manage_users` | 59 tests + 生产实测 |
| `9a966cd`/`bfeddc2` | 上述合并上线 | 生产 health/DTU 正常 |
| `619a807` | 按前端真实契约重写 workbench + 地图文档模型 + `map-monitor` | **63 tests** + 生产真实数据校验 |

已修正昨日审计中的错误结论：原报告称"`telemetry` 无 `le` 约束"，实测代码有 `le=168`；原报告把"写了代码"当成"已完成"，实际未运行——本次全部补跑。
