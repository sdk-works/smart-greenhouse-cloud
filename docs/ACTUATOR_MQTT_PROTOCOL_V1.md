# 风机/水阀 MQTT 协议 V1

## Topic

- 云端下发：`agri/device/{device_id}/command`，QoS 1，不保留。
- 设备上报：`agri/device/{device_id}/up`，QoS 1，不保留。

`device_id` 是设备硬件编号，例如 `FAN-01`、`VALVE-01`。所有时间使用带时区的 ISO 8601。

## 云端命令

```json
{
  "version": 1,
  "type": "command",
  "command_id": "cmd-20260825153000-ab12cd34",
  "device_id": "VALVE-01",
  "action": "open",
  "params": {"duration_seconds": 60},
  "issued_at": "2026-08-25T15:30:00+08:00",
  "expires_at": "2026-08-25T15:30:30+08:00"
}
```

动作：风机 `turn_on`、`turn_off`、`set_level`；水阀 `open`、`close`、`turn_on`、`turn_off`。

设备收到过期命令必须拒绝。同一 `command_id` 必须幂等处理，禁止重复启动。

## 设备回执

```json
{
  "version": 1,
  "type": "command_ack",
  "command_id": "cmd-20260825153000-ab12cd34",
  "device_id": "VALVE-01",
  "status": "executed",
  "actual_state": {"running": true, "valve_open": true},
  "timestamp": "2026-08-25T15:30:01+08:00",
  "error_message": null
}
```

合法状态：`accepted`、`executing`、`executed`、`rejected`、`failed`。设备应先回 accepted，再按实际执行结果回 executed 或 failed。

## 周期状态

```json
{
  "version": 1,
  "type": "device_status",
  "device_id": "FAN-01",
  "actual_state": {"running": false, "level": 0, "fault": false},
  "timestamp": "2026-08-25T15:31:00+08:00"
}
```

建议运行时 5 秒一次、停止时 30 秒一次；断线重连后立即上报。实际状态必须来自输出反馈或控制器确认，不能仅复制云端期望值。

