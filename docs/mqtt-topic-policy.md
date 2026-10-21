# MQTT 设备身份 / 主题约束（M1 · 只记录，本次不改 EMQX 配置）

> 本文件是**约束记录 + 提案**。本次夜间任务**未修改任何 EMQX 配置**，也未在
> 生产上做任何下发。应用层已实现的校验见 `app/core/mqtt_policy.py`。

## 1. 当前实现的主题命名空间

| 方向 | 主题 | 用途 | QoS | retain |
|---|---|---|---|---|
| 上行 | `agri/robot/{robot_id}/up` | 遥测 / 心跳 / 状态 / 图片通知 / 配置回执 | 0~1 | 否 |
| 上行 | `agri/device/{device_id}/up` | 固定设备（DTU 串口）上行 | 0~1 | 否 |
| 下行 | `agri/robot/{robot_id}/command` | 机器人指令（任务/取消） | 1 | 否 |
| 下行 | `agri/robot/{robot_id}/config` | 机器人点位配置 | 1 | **是** |
| 下行 | `agri/device/{device_id}/command` | 固定设备指令 | 1 | 否 |
| 下行 | `agri/device/{device_id}/ack` | 固定设备上行回执（`upload_ack`） | 1 | 否 |

> 注意：`app/api/devices.py::_build_mqtt_topics` 目前对外展示的主题形如
> `agri/{greenhouse_id}/{device_id}/telemetry`，与真实收发主题**不一致**。
> 这是一个已知的对外文档缺陷，本次未改（涉及前端/机器人端对接），列入待决策。

## 2. 应当成立的约束（提案，需 EMQX 侧配置生效）

1. **设备身份**
   - client_id 固定为 `device_<device_id>`。
   - 用户名与 client_id 一致；密码为每设备独立凭据（禁止共用）。
   - 未注册设备一律拒绝连接。

2. **发布权限（pub）**
   - 每个设备仅可 pub 自己的 `/up` 主题：
     `agri/robot/{self}/up`、`agri/device/{self}/up`。
   - 禁止 pub 任何其它主题，尤其禁止 pub 到别的设备的 `/up`。

3. **订阅权限（sub）**
   - 每个设备仅可 sub 自己的下行主题：
     `agri/robot/{self}/command`、`agri/robot/{self}/config`、
     `agri/device/{self}/command`、`agri/device/{self}/ack`。
   - **禁止** `#`、`+` 跨设备通配订阅；禁止 sub 其它设备的 `/up`。

4. **云端侧**
   - 云端只 sub 已注册设备的 `/up` 主题；未注册设备消息一律丢弃并记 `raw_messages`。
   - 云端在下发任何控制/任务前，必须校验目标设备归属当前操作者公司。

5. **控制下发的语义约束（M4 状态机配套）**
   - 下行命令只表示"已发出"，**不等于**设备已执行；执行结果必须由设备回执体现。
   - 见 `docs/state-machines.md`。

## 3. 本次在应用层已实现的校验（`app/core/mqtt_policy.py`）

| 函数 | 作用 | 是否已接入 |
|---|---|---|
| `validate_uplink_topic(topic, device_id)` | 上行主题必须严格等于本设备 `/up`，拒绝通配 | 提供，供 EMQX 规则/接入层调用；运行路径暂未插入 |
| `validate_client_id(client_id, device_id)` | client_id 形态校验 | 同上 |
| `assert_can_control(user, greenhouse_company_id, device_id)` | 控制下发前校验设备归属 | **已接入** `POST /devices/{id}/command`、`POST /tasks/{id}/dispatch` |

## 4. 未做的事（明早验收请勿误解）

- ❌ 未改 EMQX 配置 / ACL / 认证链。
- ❌ 未在生产上做过任何 MQTT 主题验证（本机无权操作 EMQX）。
- ❌ 未验证真实机器人是否遵守上述 client_id 形态。
