# 设备台账与安装配置清单

> 与 T3 的「安装配置清单」是同一套东西，不另写一份：
> **清单由接口生成**（`GET /api/v1/devices/{device_id}/credential/install-sheet?fmt=text`），
> 本文只说明台账字段、怎么取清单、现场怎么核。口令一律不写进台账。

## 1. 台账字段

| 字段 | 来源 | 说明 |
|---|---|---|
| 设备编号 `device_id` | `devices.device_id` | 上行主题里用的身份（如 `JY001`） |
| 设备名称 | `devices.name` | |
| 所属大棚 | `devices.greenhouse_id` | 公司归属由大棚推导（`devices` 表没有 `company_id` 列） |
| DTU 资产编号 | `mqtt_credentials.asset_no` | 贴壳上的号，安装时现场核对 |
| MQTT 用户名 | `mqtt_credentials.username` | EMQX authn 启用后设备侧要填 |
| 允许 Client ID | `mqtt_credentials.clientid` | DTU 模块序列号，唯一；不一致即被拒 |
| 兼容身份 `compat_identity` | `mqtt_credentials.compat_identity` | 老报文里的叫法，便于并轨期识别 |
| 凭据状态 | `mqtt_credentials.enabled` | 停用后设备连不上 |
| 换密次数 | `mqtt_credentials.rotation_count` | 每次换密 +1 |
| 最近换密 | `mqtt_credentials.last_rotated_at` | |

**口令不进台账**：只在"创建"或"换密"那一次返回，系统不保存明文，
现场重装必须走 `/credential/rotate` 重新生成并重新打印清单。

## 2. 现有台账（生产，只读核对，2026-10-22 23:xx）

| 设备编号 | 名称 | 所属大棚 | 资产编号 | 用户名 | Client ID | 凭据状态 |
|---|---|---|---|---|---|---|
| `JY001` | 京阳环境监测终端 | 京阳温室试点棚（`aa20d94e-…`，公司 `c0000000-…-0001`） | `jy-dtu-000001` | `dev-jy-dtu-000001` | `02501026081300004386` | 启用 |

备注（`mqtt_credentials.note` 原文）：
「DTU jy-dtu-000001 绑定现有 clientid（兼容例外，保留 02501026081300004386）；EMQX authn 未启用前不生效」

**生产库只有这一台设备、一套凭据**，其余都是测试数据。

## 3. 测试中登记的 DTU（会自己清理，不进台账）

- T3 契约测试 / T5 模拟器：在**测试库**里登记 `SIM-DTU-0001`、`dev-*`（随机编号）等，
  凭据用户名 `dev-sim-0001` / `dev-dtu-test-0001`，Client ID `SIM00000000000001` / `TEST…`，
  跑完由夹具删除。**这些不是台账条目**，只是证明流程可用。
- 若要在测试环境留一台长期可用的 DTU，用 T3 的接口登记即可，不要把编号写成 `JY001` 之类真实身份。

## 4. 新装一台 DTU 的操作顺序

1. 先在设备详情里确认设备已经存在（没有就先建设备并绑定大棚）。
2. `POST /api/v1/devices/{device_id}/credential` 传 `asset_no` + `clientid`
   → 返回的 `password` **当场抄进安装清单**，刷新后不再可见。
3. `GET .../credential/install-sheet?fmt=text` 打印清单给现场
   （清单里口令一栏是占位提示，不是真口令）。
4. 现场按清单填 DTU：**Client ID 必须与模块序列号一致**，用户名/口令区分大小写。
5. 装完回到设备详情 / 大棚状态页，确认「最近收到有效数据」开始走动。
6. 后续要重装：`POST .../credential/rotate` 重新生成口令 + 重新打印清单（旧口令立即失效）。
7. 设备退役：`PATCH .../credential/enabled {"enabled": false}`，写清原因（会落审计）。

## 5. 台账与凭据操作的可追溯性

创建 / 换密 / 停用 / 启用四次写操作都会落 `audit_logs`（`resource='mqtt_credential'`），
明细里只有口令**指纹**和原因，没有明文。可按用户名查：

```sql
SELECT l.created_at, u.username AS actor, l.action, l.resource_id
  FROM audit_logs l LEFT JOIN users u ON u.id = l.user_id
 WHERE l.resource = 'mqtt_credential'
 ORDER BY l.created_at;
```
