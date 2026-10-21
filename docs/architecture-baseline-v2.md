# 智慧温室平台 · 架构基线 v2（2026-10-21 讨论稿，未冻结）

> 状态：**未冻结**。v1 保留为历史版本；本版按 2026-10-21 审阅意见修订，重点解决三件事：
> **① 点位独立于机器人；② 地图发布版本独立于编辑修订号；③ 历史归属在采集任务中固定。**
> 本版只做设计，不含代码改动；代码改动见 §九 分批计划。

## 〇、对 v1 的审阅结论与本次修订

| # | 审阅意见 | v2 处理 |
|---|---|---|
| 1 | 不能把一个机器人永久写进巡检点 | 拆成 `inspection_points`（稳定点位）与 `robot_point_mappings`（机器人导航映射），支持一点多机器人；`binding` 仅作接口投影，不作为事实来源 |
| 2 | 编辑修订号 ≠ 历史地图版本 | 引入 `revision`（并发）、`map_versions`（发布不可变）、`inspection_tasks.snapshot`（任务快照）三层 |
| 3 | 设备绑定历史只靠起止时间不够 | `device_bindings` 增加分区/点位/通道/绑定版本 + 时间段互斥；入库保存解析归属与绑定版本；不确定进"待确认"；机器人按任务快照归属 |
| 4 | 数据质量不能是互斥枚举 | 拆为测量有效性、时间质量、位置质量、新鲜度（查询时算）、来源五个维度 |
| 5 | 公司隔离不止给 users 加 company_id | 列全隔离面（查询/导出/文件/地图任务/控制/认领/缓存/后台/MQTT 身份），先建默认公司基地再收紧；权限点独立 |
| 6 | 原始消息与照片关联要更早 | `raw_messages` 随接入落地；照片与任务/点位/时间/地图版本关联纳入机器人最小闭环；`/photo-catalog` 不是页面补丁 |
| 7 | 契约修复正确，但语义要写进测试 | 明确 PUT 语义、删除/停用、迁移方式、乐观锁原子性；前端区分空/加载/异常/失败/无权限 |

## 一、目标对象模型（v2）

### 1.1 管理与归属
```
companies(id, name, status, created_at)
bases(id, company_id, name, location, created_at)
greenhouses(id, company_id, base_id, name, location, area, crop_type, description, is_active, created_at, updated_at)
```
- `greenhouses.company_id/base_id` 由服务端写，**不接受客户端提交决定归属**。
- 迁移：先建默认公司/基地 → 回填现有大棚 → 再加非空约束。

### 1.2 巡检点（稳定，独立于机器人）
```
inspection_points(
  id, greenhouse_id, code, name, zone,
  x, y, coord_unit, map_edit_revision,
  created_at, retired_at
)
```
- 点位有稳定 `id`；名称可改，`id` 不变。
- `retired_at` 软停用：有历史数据的点位删除=停用，不物理删除。

### 1.3 机器人导航映射（一点多机器人）
```
robot_point_mappings(
  id, robot_id, robot_map_version,
  nav_point_code, point_id, greenhouse_id,
  sample_seconds, collect_soil, collect_environment, take_photo, enabled,
  effective_from, effective_to, status,   -- active / retired
  created_at
)
UNIQUE(robot_id, robot_map_version, nav_point_code, effective_from)
```
- 同一 `point_id` 可对应多个机器人的不同 `nav_point_code`。
- 查询时按 `effective_to IS NULL` 取当前映射；历史映射保留。
- **`binding` 是只读投影**：接口返回 `bindings: [{robot_id, nav_point_code, mapping_id}]`，兼容旧字段 `binding`（取第一条）。地图文档不再存 `binding`，消除双写。

### 1.4 地图版本（三层）
| 层 | 存储 | 用途 |
|---|---|---|
| 编辑修订号 | `greenhouses.map_data.revision` | 并发更新检查（PUT 时） |
| 已发布版本 | `map_versions(id, greenhouse_id, version, layout, defaults, points_snapshot, published_at, published_by, immutable)` | 发布后不可变，巡检执行引用 |
| 任务快照 | `inspection_tasks.snapshot(JSON)` | 保存本次实际路线/点位/采集要求 |

- 编辑不自动发布；发布生成新的 `map_versions` 行（`version+1`，不可变）。
- 任务创建时把当次 `map_version` 与点位参数写入 `snapshot`；**执行期间改点位不影响在跑任务**。

### 1.5 设备绑定历史
```
device_bindings(
  id, device_id, greenhouse_id, point_id, channel,
  start_at, end_at, binding_version, created_at
)
-- 约束：同一 device_id+channel 的 [start_at,end_at) 不重叠（PostgreSQL exclusion constraint）
```
- 入库时：按 `received_at` 解析归属 → 记录 `attribution_status(resolved/pending)`、`binding_version`、`point_id`。
- 归属不确定（未校时/补传/跨绑定窗口）→ `pending`，不猜。
- 机器人数据**优先按任务快照**确定归属，不套用固定设备安装绑定。

### 1.6 原始消息与证据
```
raw_messages(id, msg_id, source, device_id, received_at,
             payload_ref, parse_status, parser_version, error)
inspection_evidence(id, task_id, point_id, map_version_id,
                    captured_at, media_ref, media_type, created_at)
```
- 原始报文可设保留期（如 90 天），但接入框架先落这三件事：消息标识/来源/接收时间、原始内容或可追溯引用、解析状态/解析器版本/失败原因。
- 照片关联：`inspection_evidence` 同时带 `task_id / point_id / captured_at / map_version_id`，`/photo-catalog` 据此查询。

## 二、数据质量（正交维度，不互斥）

| 维度 | 取值 | 存储/计算 |
|---|---|---|
| 测量有效性 | valid / invalid + reason | 存储 |
| 时间质量 | synced / unsynced / estimated | 存储 |
| 位置质量 | fixed_bound / reliable / uncertain | 存储 |
| 新鲜度 | fresh / stale | **查询时按 sampled_at/created_at 计算**，不落库为原始状态 |
| 数据来源 | fixed / robot / manual | 存储 |
| 归属状态 | resolved / pending | 存储 |

示例：温度读数有效（measurement=valid），但时间未同步（time=unsynced）、位置待确认（position=uncertain）——三个维度同时成立。

## 三、坐标契约（v2 补充）

- 单位：米（`coord_unit="m"`）。
- 原点：大棚西南角（south-west）。
- 轴向：X 向东为正，Y 向北为正。
- 允许范围：`0 ≤ x ≤ layout.width`，`0 ≤ y ≤ layout.height`；越界拒绝保存。
- `calibrated=false`：坐标仅作示意图，**不得**直接用于导航；`calibrated=true` 才可与机器人地图对应。
- 平台布局图与机器人导航地图的对应关系需**显式标定记录**（至少：标定点、变换参数、标定时间、操作者），不得隐式假设。

## 四、接口语义（写进契约测试）

| 问题 | v2 决定 |
|---|---|
| 地图 PUT 是替换还是合并 | **完整替换可编辑字段**（layout/defaults/points），`revision` 乐观锁；未出现在 payload 的点视为删除或停用（见下） |
| 删除点位如何表达 | payload 中缺失该 `point_id` = 删除。**有历史记录 → 置 `retired_at`（停用）；无历史 → 物理删除** |
| 历史点位能否删 | 不能物理删；`retired_at` 后仍可查历史，列表默认过滤 |
| 旧地图迁移 | **一次性持久化迁移**（读取时兜底仅作过渡），迁移后写 `schema_version=2`，避免长期双形状 |
| 乐观锁是否原子 | **数据库原子更新**：`UPDATE ... WHERE id=? AND revision=?`，`rowcount=0` → 409，禁止"先读后写" |
| 绑定写入 | 只写 `robot_point_mappings`；地图文档不存 `binding`；读取时投影 |
| 前端状态 | 必须区分：空（无点位）/ 加载中 / 格式异常 / 请求失败 / 无权限；不能一律白屏 |

## 五、权限与隔离（v2 补全）

- 角色：公司管理员 / 基地大棚负责人 / 操作人员 / 只读。**操作人员不天然拥有全部控制权**。
- 独立权限点（示例）：`view_data`、`start_inspection`、`control_fan`、`control_irrigation`、`edit_device_config`、`manage_users`、`edit_point_mapping`、`publish_map`、`mqtt_config`。
- 隔离面（验收必须逐项覆盖）：
  1. 数据查询与历史导出；2. 图片/视频/文件下载；3. 地图、点位、机器人任务；4. 控制下发；5. 设备认领与换绑；6. 缓存键（含 company 前缀）；7. 后台任务；8. MQTT 设备身份与主题权限（不能只信报文里的编号）。
- 归属写入：服务端从当前用户/设备身份推导，**忽略客户端提交的 company_id**。

## 六、状态机（v2 保持并补流转校验）

| 对象 | 状态 |
|---|---|
| 巡检任务 | 待执行 / 执行中 / 暂停 / 完成 / 部分完成 / 失败 / 取消 |
| 控制命令 | 待发送 / 已发送 / 已接收 / 执行中 / 成功 / 失败 / 超时 / 取消 |
| 异常事件 | 待确认 / 已确认 / 处理中 / 待复核 / 关闭 |
| 归属 | resolved / pending |

约束：ACK≠执行结果；走完路线≠点位采集成功；环境恢复≠事件关闭；服务重启后"执行中"需可恢复或标记失败。

## 七、契约测试矩阵（新增）

1. PUT 完整替换：删除未提交点、保留已提交点。
2. 有历史点位删除 → 停用（`retired_at` 非空），历史仍可查。
3. 无历史点位删除 → 物理删除。
4. `revision` 不匹配 → 409；并发两写只有一个成功（原子）。
5. 旧地图（`{width,height,waypoints}`）迁移后 `schema_version=2`，幂等。
6. 一点多机器人：同一 `point_id` 两条 mapping，接口 `bindings` 返回两条。
7. 绑定投影：地图文档不存 binding；`GET` 时由 mapping 投影。
8. 数据质量五维度可同时成立（valid + unsynced + uncertain）。
9. 补传旧消息按 `received_at` 归属，跨绑定窗口 → `pending` 而非猜测。
10. 任务快照：执行中修改点位/参数，任务仍按快照运行。
11. 空/加载/异常/失败/无权限 五种前端状态（接口侧提供区分依据）。

## 八、实施顺序（采纳审阅建议）

| 批次 | 内容 | 交付判定 |
|---|---|---|
| **第一批：固定基础关系** | 公司/基地及数据范围；稳定点位表；设备绑定历史；机器人导航映射；数据契约与质量维度；原始消息表随接入落地 | 现有数据迁移到默认公司/基地；一点多机器人可表达；入库归属带版本 |
| **第二批：机器人最小闭环** | 已发布地图版本；任务快照；任务状态流转；数值与照片归档（`/photo-catalog` 随此批） | "JY001 固定采集＋一台机器人跨两个巡检点"跑通 |
| **第三批：运行可靠性** | 原始消息追溯完善；补传归属；重启恢复；异常事件闭环 | 断网补传不冒充实时值；重启不永久卡执行中 |
| **第四批：业务扩展** | 种植批次；设备控制完善；AI 受控接口；报表 | — |

**首个真实场景验收**：JY001 固定采集 + 一台机器人跨两个巡检点，走通"创建→绑定→展示→任务→数值与照片→同棚双来源→异常处理"。

## 九、当前代码与 v2 的差距（需改，未改）

| 现状 | v2 要求 | 影响 |
|---|---|---|
| `map_data.points[].binding` 与 `robot_point_configs` 双写 | 只写 mapping，读取投影 | 双写易不一致（当前确实存在） |
| `robot_point_configs` 无 `point_id`，按 `point_name` 匹配 | 增加 `point_id` + 映射表 | 点位改名会断关联 |
| 仅 `revision`，无发布版本/任务快照 | 三层 | 改地图影响在跑任务 |
| `telemetry` 无 `company_id/map_version/raw_message_id/position_quality` | 补齐 | 无法隔离/追溯 |
| 无 `raw_messages`、`inspection_evidence`、`alerts` | 新建 | 无法重放/归档/闭环 |
| 无 `companies/bases`，`users` 无 `company_id` | 新建/加列 | 无法按公司隔离 |
| 无 `/photo-catalog` | 随第二批实现 | 照片页第 2 页起 404 |

## 十、未决问题（需拍板后才冻结）

1. 点位坐标是否一律米制，还是允许"示意坐标"与"米制坐标"两种模式并存？
2. 有历史点位删除时，前端是否显示"已停用"而非消失？
3. 机器人导航映射的 `robot_map_version` 由机器人上报还是平台登记？
4. 原始消息保留期（90 天？按容量？）。
5. 公司隔离是否要求"跨公司完全不可见"，还是允许总部只读汇总？
