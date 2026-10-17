#!/usr/bin/env python3
"""
固定终端(JY_EnvMon) 模拟器 — 契约 v1.2
发送到:  agri/device/{device_id}/up
订阅:    agri/device/{device_id}/ack
验证场景:
  1. 正常上报 → 收到 stored ACK
  2. 重复上报同 msg_id → 云端只存一条,仍回 stored
  3. 同 msg_id 不同内容 → 云端冲突拒绝,不回 stored
  4. 未知采样时间(time_quality=0, sampled_at=0) → 云端 sampled_at 存 NULL
用法:
  python sim_device.py                    # 跑全部场景
  python sim_device.py --quick            # 只发3条正常遥测
"""
import json, time, argparse, uuid
import paho.mqtt.client as mqtt

MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_USER = "admin"
MQTT_PASS = "jyws12345"
DEVICE_ID = "JY001"          # 需已在 devices 表注册
TOPIC_UP  = f"agri/device/{DEVICE_ID}/up"
TOPIC_ACK = f"agri/device/{DEVICE_ID}/ack"

# boot_id 每次运行给一个不同值，避免 msg_id 与历史数据冲突
boot_id = 88
seq = 0
acked = []          # 收到的 ACK msg_id
rejected = []       # 收到冲突/拒绝

def next_msg_id():
    global seq
    seq += 1
    return f"{DEVICE_ID}/{boot_id}/{seq}"

def telemetry(msg_id, *, time_quality=1, sampled_at=None, data=None):
    return {
        "type": "telemetry", "schema_version": 1,
        "device_id": DEVICE_ID, "msg_id": msg_id,
        "boot_id": boot_id, "seq": int(msg_id.split("/")[2]),
        "sampled_at": sampled_at if sampled_at is not None else (0 if time_quality == 0 else int(time.time())),
        "time_quality": time_quality,
        "uptime_ms": 259200000,
        "data": data if data is not None else {
            "temperature": 21.5, "humidity": 54.5, "co2": 603,
            "light": 1375, "pressure": 101.6,
            "soil_temperature": 20.6, "soil_moisture": 45.0, "soil_ec": 1200.0,
            "valid": [1,1,1,1,1,1,1,1],
        },
    }

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✅ MQTT 连接成功, 订阅 {TOPIC_ACK}")
        client.subscribe(TOPIC_ACK, qos=1)
    else:
        print(f"❌ 连接失败 code={rc}")

def on_ack(client, userdata, msg):
    try:
        ack = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        print(f"[ACK] 非JSON: {msg.payload!r}"); return
    if ack.get("type") == "upload_ack":
        s = ack.get("status")
        print(f"  ◀ ACK msg_id={ack.get('msg_id')} status={s}")
        if s == "stored":
            acked.append(ack.get("msg_id"))
        else:
            rejected.append(ack.get("msg_id"))

def pub(client, payload, tag):
    client.publish(TOPIC_UP, json.dumps(payload, ensure_ascii=False), qos=1)
    print(f"  ▶ [{tag}] {payload['msg_id']}")

def run_quick(client):
    print("\n=== QUICK: 3 条正常遥测 ===")
    for _ in range(3):
        m = next_msg_id()
        pub(client, telemetry(m), "telemetry")
        time.sleep(1)
    time.sleep(2)
    print(f"ACK 收到 {len(acked)} 条: {acked}")

def run_full(client):
    print("\n=== 场景1: 正常上报 → stored ===")
    m1 = next_msg_id()
    pub(client, telemetry(m1), "telemetry")
    time.sleep(2)

    print("\n=== 场景2: 重复上报同 msg_id → 云端只存一条,仍回 stored ===")
    pub(client, telemetry(m1), "repeat")
    time.sleep(2)

    print("\n=== 场景3: 同 msg_id 不同内容 → 冲突拒绝,不回 stored ===")
    m2 = next_msg_id()
    pub(client, telemetry(m2, data={"temperature": 99.9, "valid": [1]*8}), "conflict-A")
    time.sleep(2)
    pub(client, telemetry(m2, data={"temperature": 10.0, "valid": [1]*8}), "conflict-B")
    time.sleep(2)

    print("\n=== 场景4: 未知采样时间(time_quality=0) ===")
    m3 = next_msg_id()
    pub(client, telemetry(m3, time_quality=0, sampled_at=0), "no-rtc")
    time.sleep(2)

    print("\n=== 场景5: 合法零值(light=0) + 无效null ===")
    m4 = next_msg_id()
    pub(client, telemetry(m4, data={
        "temperature": 0.0, "humidity": None, "co2": 0,
        "light": 0, "valid": [1,0,1,1,0,1,1,1],
    }), "zero-null")
    time.sleep(2)

    print(f"\n=== 汇总: stored ACK {len(acked)} 条 {acked}; 冲突/拒绝 {len(rejected)} 条 {rejected} ===")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    client = mqtt.Client(client_id=f"sim-dev-{uuid.uuid4().hex[:6]}")
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.message_callback_add(TOPIC_ACK, on_ack)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
    except Exception as e:
        print(f"❌ 无法连接 broker: {e}"); return
    client.loop_start()
    time.sleep(1)
    if args.quick:
        run_quick(client)
    else:
        run_full(client)
    client.loop_stop()
    client.disconnect()

if __name__ == "__main__":
    main()
