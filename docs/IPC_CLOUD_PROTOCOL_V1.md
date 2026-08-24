# 工控机—云端通信协议 V1

状态：开发基线，2026-08-24。该协议用于第三方导航、巡检工控机和云端平台联调。

## 1. 链路职责

- 导航串口：第三方导航向工控机发送到点任务；工控机返回接收、运行、完成或故障状态。
- MQTT：工控机通过网络向云端上报心跳、任务状态和采集结果。
- 图片服务：图片保持独立 TCP/HTTP 上传；MQTT 只发送图片上传结果和 URL。
- 土壤 RS-485：只用于读取七参数传感器，不与导航或云端通信复用串口。

## 2. 导航串口协议

导航到点：

```text
ARR|{task_id}|{point_code}|{sample_seconds}\n
```

工控机回复：

```text
ACK|{task_id}\n
RUNNING|{task_id}|state={state}\n
DONE|{task_id}|saved=1|upload={queued|pending}\n
FAULT|{task_id}|{error_code}\n
BUSY|{task_id}|active={active_task_id}\n
```

`DONE` 表示采集设备已安全归位或当前无机械臂模式已完成采集，并且结果已经可靠保存到本机。云端暂时不可达不能阻塞小车继续运行。

## 3. MQTT Topic

```text
上行：agri/robot/{robot_id}/up
命令：agri/robot/{robot_id}/command
配置：agri/robot/{robot_id}/config
```

QoS 使用 1。上行消息必须包含全局唯一 `msg_id`，云端据此去重。

## 4. 七参数采集结果

```json
{
  "type": "telemetry",
  "msg_id": "550e8400-e29b-41d4-a716-446655440000",
  "robot_id": "ROB001",
  "task_id": "TASK-20260824-001",
  "timestamp": "2026-08-24T08:00:00Z",
  "data": {
    "soil_moisture": 42.1,
    "soil_temperature": 23.6,
    "soil_ec": 518,
    "ph": 6.8,
    "soil_nitrogen": 41,
    "soil_phosphorus": 27,
    "soil_potassium": 65,
    "sample_count": 10
  },
  "robot_status": {
    "status": "sampling_complete"
  },
  "navigation": {
    "point_mapping": {
      "chassis_point": "P10",
      "greenhouse_id": "GH001",
      "greenhouse_point": "G20",
      "mapping_id": "MAP001"
    }
  }
}
```

## 5. 字段单位

| 字段 | 含义 | 单位 |
|---|---|---|
| `soil_moisture` | 土壤含水率 | % |
| `soil_temperature` | 土壤温度 | ℃ |
| `soil_ec` | 电导率 | μS/cm，按传感器原始标定 |
| `ph` | 酸碱度 | pH |
| `soil_nitrogen` | 氮 | mg/kg |
| `soil_phosphorus` | 磷 | mg/kg |
| `soil_potassium` | 钾 | mg/kg |
| `sample_count` | 本次汇总有效样本数 | 个 |

上传值取工控机采样汇总中的 `mean`；完整逐次采样保留在工控机本地任务库，不通过每条遥测重复上传。

## 6. 任务状态

```text
IDLE → ARRIVED → PRECHECK → [DEPLOY → STABILIZE] → SAMPLE
     → [RETRACT] → LOCAL_SAVED → DONE → IDLE
```

方括号内状态只在机械臂启用时存在。任一步骤失败进入 `FAULT`。相同 `task_id` 重发时不得重复执行已经完成的采集任务。

## 7. 兼容规则

- 云端继续接受原有温湿度、光照和 CO₂ 字段。
- 七参数既写入独立数据库列，也保存在 `raw_data` 中。
- 缺少点位映射时允许保存数据，但记录告警；正式联调前必须配置点位映射。
- 工控机断网时写入本地 outbox，恢复连接后按原 `msg_id` 重发。
