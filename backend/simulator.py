#!/usr/bin/env python3
"""
模拟器 - 匹配智慧农业平台 V10 MQTT 格式
发送到：agri/robot/ROB001/up
功能：
  1. 每5秒发送环境遥测数据
  2. 每60秒自动发送心跳包
"""
import json
import random
import time
import signal
import uuid
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

# ====================== 配置 ======================
MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_USER = "admin"
MQTT_PASS = "SET_LOCALLY_DO_NOT_COMMIT"
ROBOT_ID = "ROB001"          # 必须和后端一致
GREENHOUSE_ID = "GH001"      # 必须存在
TELEMETRY_INTERVAL = 5      # 遥测间隔（秒）
HEARTBEAT_INTERVAL = 60     # 心跳间隔（秒）
# ===================================================

client = mqtt.Client(client_id=f"sim-{ROBOT_ID}")
running = True
last_heartbeat_time = 0

def signal_handler(sig, frame):
    global running
    print("\n🛑 停止模拟器")
    running = False
    client.disconnect()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ MQTT 连接成功")
    else:
        print(f"❌ 连接失败，code={rc}")

# ---------------------- 发送遥测数据 ----------------------
def send_telemetry():
    payload = {
        "type": "telemetry",
        "msg_id": str(uuid.uuid4()),
        "task_id": f"SIM-{int(time.time())}",
        "timestamp": int(datetime.now().timestamp()),
        "greenhouse_id": GREENHOUSE_ID,
        "data": {
            "temperature": round(20 + random.uniform(-3, 5), 2),
            "humidity": round(60 + random.uniform(-5, 10), 2),
            "soil_moisture": round(45 + random.uniform(-5, 5), 2),
            "soil_temperature": round(19 + random.uniform(-2, 4), 2),
            "soil_ec": random.randint(300, 900),
            "soil_nitrogen": random.randint(20, 80),
            "soil_phosphorus": random.randint(15, 60),
            "soil_potassium": random.randint(30, 100),
            "sample_count": 10,
            "light_intensity": random.randint(800, 2000),
            "co2": 400 + random.randint(-30, 50),
            "co2_level": 400 + random.randint(-30, 50),
            "ph": round(6.0 + random.uniform(0, 1.5), 2)
        },
        "robot_status": {
            "battery": 80 + random.randint(-5, 10),
            "position": {
                "x": round(random.uniform(50, 150), 2),
                "y": round(random.uniform(200, 400), 2)
            }
        },
        "navigation": {
            "point_mapping": {
                "chassis_point": "P10",
                "greenhouse_point": "G20",
                "greenhouse_id": GREENHOUSE_ID,
                "mapping_id": "MAP001"
            }
        }
    }

    topic = f"agri/robot/{ROBOT_ID}/up"
    client.publish(topic, json.dumps(payload), qos=0)

    d = payload["data"]
    r = payload["robot_status"]
    pos = r["position"]
    print(f"📤 遥测 → {topic}")
    print(f"🌡 温度:{d['temperature']}°C | 💧湿度:{d['humidity']}% | 🌱土壤:{d['soil_moisture']}%")
    print(f"🌡土温:{d['soil_temperature']}°C | EC:{d['soil_ec']} | pH:{d['ph']}")
    print(f"N/P/K:{d['soil_nitrogen']}/{d['soil_phosphorus']}/{d['soil_potassium']} mg/kg")
    print(f"🔋电池:{r['battery']}% | 📍位置:({pos['x']}, {pos['y']})\n")

# ---------------------- 发送心跳（每60秒） ----------------------
def send_heartbeat():
    global last_heartbeat_time
    now = time.time()

    if now - last_heartbeat_time >= HEARTBEAT_INTERVAL:
        payload = {
            "type": "heartbeat",
            "robot_id": ROBOT_ID,       # 严格按你要求的格式
            "timestamp": int(now),
            "battery": 78 + random.randint(-2, 2),  # 轻微波动
            "status": random.choice(["idle", "running", "patrolling"])
        }

        topic = f"agri/robot/{ROBOT_ID}/up"
        client.publish(topic, json.dumps(payload), qos=0)
        print(f"💓 心跳 → {topic}")
        print(f"   机器人:{payload['robot_id']}  状态:{payload['status']}  电池:{payload['battery']}%\n")
        
        last_heartbeat_time = now

# ---------------------- 主循环 ----------------------
def main():
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    signal.signal(signal.SIGINT, signal_handler)

    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
    except Exception as e:
        print(f"❌ 无法连接 MQTT broker：{e}")
        return

    client.loop_start()
    print(f"\n🚀 模拟器启动成功！")
    print(f"📡 遥测每 {TELEMETRY_INTERVAL} 秒发送一次")
    print(f"💓 心跳每 {HEARTBEAT_INTERVAL} 秒发送一次\n")

    while running:
        send_telemetry()   # 发环境数据
        send_heartbeat()   # 自动发心跳（内部判断60秒）
        time.sleep(TELEMETRY_INTERVAL)

if __name__ == "__main__":
    main()
