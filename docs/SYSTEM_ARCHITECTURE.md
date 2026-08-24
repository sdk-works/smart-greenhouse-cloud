# Smart Greenhouse Platform v6 — 系统架构文档

## MQTT Topic 规范

### 新格式（v6推荐）
```
上行: agri/{greenhouse_id}/{device_id}/telemetry   QoS 0
上行: agri/{greenhouse_id}/{device_id}/status       QoS 1
下行: agri/{greenhouse_id}/{device_id}/command      QoS 1
```

### 旧格式（兼容）
```
agri/{device_id}/telemetry
agri/{device_id}/status
```

## 时间戳规范

全链路统一 UTC：
- 设备端：datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
- 后端：_parse_timestamp() 统一转 UTC datetime with tzinfo
- 数据库：TIMESTAMPTZ
- 前端：dayjs.utc(ts).local() 转本地时间显示

## 数据格式

```json
{
  "device_id": "robot_001",
  "greenhouse_id": "gh_001",
  "timestamp": "2026-03-24T20:00:00Z",
  "type": "telemetry",
  "data": {
    "temperature": 25.6,
    "humidity": 60.0,
    "co2": 800,
    "light": 12000,
    "soil_moisture": 45.0,
    "ph": 6.8
  },
  "robot_status": {
    "battery": 85.2,
    "speed": 0.5,
    "status": "patrolling",
    "position": { "x": 30.5, "y": 15.2 }
  },
  "image": null
}
```

## 部署

```bash
sudo bash install.sh   # 全新安装
sudo bash update.sh    # 代码更新
bash status.sh         # 服务状态
```

## 模拟器

```bash
cd simulator/
pip install paho-mqtt
python3 robot_simulator.py --host localhost --devices 2 --interval 10
```
