#!/usr/bin/env python3
"""
robot_simulator.py — Smart Greenhouse Robot Simulator v6
每10秒上传遥测数据，每30秒上传图片模拟
时间戳统一 UTC ISO 8601 (Z后缀)
"""
import json, math, random, signal, sys, time, argparse
from datetime import datetime, timezone
import paho.mqtt.client as mqtt


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_telemetry(device_id, greenhouse_id, pos_x, pos_y, battery):
    hour  = datetime.now().hour
    t_off = 4 * math.sin((hour - 6) * math.pi / 12)
    light = max(0, 20000 * math.sin((hour - 6) * math.pi / 14)) if 6 <= hour <= 20 else 0
    return {
        "device_id":    device_id,
        "greenhouse_id":greenhouse_id,
        "timestamp":    utc_now(),
        "type":         "telemetry",
        "data": {
            "temperature":   round(23 + t_off + random.gauss(0, 0.5), 2),
            "humidity":      round(68  + random.gauss(0, 3),   2),
            "co2":           round(420 + random.gauss(0, 40),  1),
            "light":         round(light + random.gauss(0, 300), 1),
            "soil_moisture": round(45  + random.gauss(0, 5),   2),
            "ph":            round(6.5 + random.gauss(0, 0.2), 2),
        },
        "robot_status": {
            "battery":  round(max(5, battery), 1),
            "speed":    round(random.uniform(0.4, 0.8), 2),
            "status":   "charging" if battery < 20 else "patrolling",
            "position": {"x": round(pos_x, 2), "y": round(pos_y, 2)},
        },
        "image": None,
    }


def make_image(device_id, greenhouse_id, pos_x, pos_y, count):
    p = make_telemetry(device_id, greenhouse_id, pos_x, pos_y, 85)
    p["type"] = "image"
    p["robot_status"]["status"] = "capturing"
    p["image"] = {
        "url":      f"http://localhost:8000/uploads/{device_id}/sim_{count:04d}.jpg",
        "filename": f"sim_{count:04d}.jpg",
        "size":     random.randint(50000, 200000),
        "position": {"x": round(pos_x, 2), "y": round(pos_y, 2)},
    }
    return p


def main():
    ap = argparse.ArgumentParser(description="Smart Greenhouse Robot Simulator v6")
    ap.add_argument("--host",       default="localhost",   help="MQTT Broker 地址")
    ap.add_argument("--port",       default=1883, type=int)
    ap.add_argument("--username",   default="admin")
    ap.add_argument("--password",   default="public")
    ap.add_argument("--prefix",     default="agri",        help="Topic 前缀")
    ap.add_argument("--greenhouse", default="gh_001",      help="大棚ID")
    ap.add_argument("--devices",    default=2, type=int,   help="模拟设备数量")
    ap.add_argument("--interval",   default=10, type=int,  help="遥测间隔（秒）")
    ap.add_argument("--old-topic",  action="store_true",   help="使用旧格式 topic")
    args = ap.parse_args()

    gh    = args.greenhouse
    pfx   = args.prefix
    ivl   = args.interval
    use_new = not args.old_topic

    def topic(dev_id, suffix):
        if use_new:
            return f"{pfx}/{gh}/{dev_id}/{suffix}"
        return f"{pfx}/{dev_id}/{suffix}"

    robots = [
        {"id": f"robot_{i+1:03d}", "x": random.uniform(10,80),
         "y": random.uniform(10,50), "batt": random.uniform(75,95), "img": 0}
        for i in range(args.devices)
    ]

    print("=" * 55)
    print("  Smart Greenhouse Robot Simulator v6")
    print("=" * 55)
    print(f"  Broker:    {args.host}:{args.port}")
    print(f"  Topic fmt: {'新 agri/{gh}/{dev}/...' if use_new else '旧 agri/{dev}/...'}")
    print(f"  Greenhouse:{gh}   Devices:{args.devices}   Interval:{ivl}s")
    print("=" * 55)

    clients = {}
    for r in robots:
        c = mqtt.Client(client_id=f"sim-{r['id']}-{random.randint(1000,9999)}")
        if args.username:
            c.username_pw_set(args.username, args.password)

        def on_connect(client, ud, flags, rc, _r=r):
            if rc == 0:
                print(f"  ✅ [{_r['id']}] 连接成功")
                client.publish(topic(_r["id"], "status"),
                               json.dumps({"online": True, "device_id": _r["id"], "timestamp": utc_now()}), qos=1)
                client.subscribe(topic(_r["id"], "command"), qos=1)
            else:
                codes = {1:"协议版本",2:"ID被拒",3:"服务不可用",4:"认证失败",5:"未授权"}
                print(f"  ❌ [{_r['id']}] 失败 rc={rc} {codes.get(rc,'未知')}")

        def on_message(client, ud, msg, _r=r):
            try:
                cmd = json.loads(msg.payload.decode())
                print(f"\n  📨 [{_r['id']}] 收到指令: {cmd.get('command')}")
                if cmd.get("task"):
                    print(f"     task: {json.dumps(cmd['task'], ensure_ascii=False)}")
            except Exception:
                pass

        c.on_connect = on_connect
        c.on_message = on_message
        try:
            c.connect(args.host, args.port, keepalive=60)
            c.loop_start()
            clients[r["id"]] = c
        except Exception as e:
            print(f"  ❌ [{r['id']}] 连接失败: {e}")

    time.sleep(1.5)
    tick    = 0
    running = True

    def stop(s, f):
        nonlocal running
        running = False
        print("\n\n🛑 停止模拟器...")
        for r in robots:
            c = clients.get(r["id"])
            if c:
                c.publish(topic(r["id"], "status"),
                          json.dumps({"online": False, "device_id": r["id"], "timestamp": utc_now()}), qos=1)
        time.sleep(0.5)
        sys.exit(0)

    signal.signal(signal.SIGINT,  stop)
    signal.signal(signal.SIGTERM, stop)

    print(f"\n🚀 开始推送（遥测={ivl}s，图片=30s，Ctrl+C 停止）\n")
    while running:
        tick += 1
        for r in robots:
            # 位置随机游走
            r["x"] = max(5, min(95, r["x"] + random.uniform(-2, 2)))
            r["y"] = max(5, min(55, r["y"] + random.uniform(-2, 2)))
            r["batt"] = max(5, r["batt"] - 0.02 * ivl)
            if r["batt"] < 20:
                r["batt"] = min(100, r["batt"] + 1.0 * ivl)  # 充电

            c = clients.get(r["id"])
            if not c or not c.is_connected():
                continue

            # ── 遥测 ──
            payload = make_telemetry(r["id"], gh, r["x"], r["y"], r["batt"])
            res = c.publish(topic(r["id"], "telemetry"), json.dumps(payload), qos=0)
            d   = payload["data"]
            rs  = payload["robot_status"]
            icon = "🔋" if rs["status"] == "charging" else "🤖"
            print(
                f"  {icon} #{tick:04d} [{r['id']}]"
                f" T={d['temperature']:.1f}°C H={d['humidity']:.1f}%"
                f" CO₂={d['co2']:.0f}ppm 🔋{rs['battery']:.0f}%"
                f" pos=({rs['position']['x']:.1f},{rs['position']['y']:.1f})"
                f" {'✅' if res.rc == 0 else '❌'}"
            )

            # ── 图片（每 30s）──
            img_every = max(1, 30 // ivl)
            if tick % img_every == 0:
                r["img"] += 1
                img_p = make_image(r["id"], gh, r["x"], r["y"], r["img"])
                c.publish(topic(r["id"], "telemetry"), json.dumps(img_p), qos=0)
                print(f"  📷 [{r['id']}] 图片上报: {img_p['image']['filename']}")

        time.sleep(ivl)


if __name__ == "__main__":
    main()
