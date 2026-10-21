# 机器人对接契约 v1（robot-integration-v1）

> 面向机器人/设备端。**本次夜间任务没有对真实机器人做过任何联调**，
> 本文描述的是云端已实现的契约；与真机的联调状态见 `docs/acceptance.md`。

## 1. MQTT 通道

见 `docs/mqtt-topic-policy.md`（约束记录 + 提案，**本次未改 EMQX 配置**）。

| 方向 | 主题 |
|---|---|
| 上行 | `agri/robot/{robot_id}/up`、`agri/device/{device_id}/up` |
| 下行 | `agri/robot/{robot_id}/command`、`agri/robot/{robot_id}/config` |
| 下行 | `agri/device/{device_id}/command`、`agri/device/{device_id}/ack` |

上行消息体按 `type` 分发：`telemetry` / `heartbeat` / `state` / `image` / `config_ack`。

## 2. 上行：遥测

```json
{"type":"telemetry","msg_id":"...","timestamp":"2026-10-21T10:00:00Z",
 "time_quality":1,"sampled_at":"2026-10-21T09:59:58Z",
 "data":{"temperature":24.5,"humidity":60,"co2":800,"light":12000},
 "robot_status":{"battery":80,"position":{"x":1.0,"y":2.0}},
 "navigation":{"point_mapping":{"chassis_point":"A点",
     "greenhouse_id":"...","greenhouse_point":"1号点"}}}
```

云端处理顺序：
1. `msg_id` 去重（冲突则拒绝）；
2. 写入 `raw_messages`（原始报文留痕）；
3. 按**绑定窗口**判定该时刻归属（补传用 `sampled_at`）；
4. 落 `telemetry` 并同步到点到记录 `inspection_visits`；
5. 回 `stored` ACK（`agri/device/{id}/ack`）。

## 3. 上行：图片通知

```json
{"type":"image","image_url":"https://.../a.jpg","thumbnail_url":"...",
 "task_id":"...","captured_at":"...",
 "navigation":{"point_mapping":{"chassis_point":"A点","greenhouse_id":"...",
     "greenhouse_point":"1号点"}}}
```

云端：
- 按 `robot_point_mappings(robot_id, nav_point_code)` 优先解析点位，
  其次按 `inspection_points.name` 匹配 `greenhouse_point`；
- **解析不出就留 `point_id=NULL`，证据标 `attribution_status=pending`，不猜**；
- 同时落 `inspection_evidence`（含 `map_version_id`）。

## 4. 下行：点位配置（retain）

```json
{"type":"point_config","schema_version":"1.0","robot_id":"ROB001",
 "config_version":3,"mode":"operational","points":[...],
 "issued_at":"...","config_sha256":"..."}
```

机器人回执：

```json
{"type":"config_ack","config_version":3,
 "point_mapping_sync_status":"synced","robot_map_version":"map-v1"}
```

云端处理：
- 更新 `robot_point_config_releases.sync_status = applied|rejected`；
- `robot_map_version` 与平台登记值不一致时**只落告警 `robot_map_alerts`，
  绝不自动改任何配置**（见下一节）。

## 5. 地图版本登记与核对

- 平台侧：`PUT /api/v1/point-config/robots/{robot_id}/map-version` 登记期望版本。
- 机器人侧：在 `config_ack`/上行里带 `robot_map_version`。
- 不一致 → `robot_map_alerts`（status=open），**需人工闭环**：
  `POST /api/v1/point-config/robots/map-alerts/{id}/resolve`。
- 未登记期望值或未回传版本 → 判定 `unknown`，**不产生告警**（不能凭空判定不一致）。

## 6. 地图发布版本（独立于编辑）

- 编辑：`PUT /api/v1/point-config/maps/{gh}`（带 `revision` 乐观锁）。
- 发布：`POST /api/v1/point-config/maps/{gh}/publish` → 快照写入 `map_versions`，
  `version = max+1`。**发布后没有任何修改/删除接口**（不可变）。
- 读取：`GET .../versions`、`GET .../versions/{v}`。
- 巡检任务创建时把 `map_version` + 点位列表 + 采集参数冻结进 `robot_tasks.snapshot`，
  **后续编辑不影响快照**。

## 7. 任务

- 创建：`POST /api/v1/tasks/`（同时生成快照）。
- 下发：`POST /api/v1/tasks/{id}/dispatch` → `running`。
- 状态流转：`POST /api/v1/tasks/{id}/status`，非法流转 400。
- 照片归档查询：`GET /api/v1/photo-catalog`。

## 8. 未做的事（请在验收时区分）

- ❌ 未连接过真实机器人/EMQX 下发过任何指令（本次全部为模拟数据 + 测试库对象）。
- ❌ 未验证真机是否遵守 client_id / 主题约束。
- ❌ 未验证真机对 `config_ack` 中 `robot_map_version` 的支持情况。
