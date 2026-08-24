import socket
import struct
import os
import hashlib
import threading
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models.models import Image, Device
from app.core.config import settings
import urllib.parse

# TCP 配置
TCP_HOST = "0.0.0.0"
TCP_PORT = 9999
BUFFER_SIZE = 4096
MAGIC = 0x12345678
HEADER_FORMAT = "!I I d d 32s 256s"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# 数据库同步引擎（修复启动错误）
def get_sync_db_url():
    return settings.DATABASE_URL.replace("+asyncpg", "")

engine = create_engine(get_sync_db_url())

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path

# 同步写入数据库，绝对稳定
def save_to_db(device_id, filename, filepath, px, py, file_hash):
    print(f"[DB] 写入设备: {device_id}, 文件: {filename}")
    try:
        with Session(engine) as db:
            device = db.query(Device).filter(Device.device_id == device_id).first()
            if not device:
                print(f"[DB] 设备不存在: {device_id}")
                return

            file_size = os.path.getsize(filepath)
            storage_url = f"/uploads/{device_id}/{filename}"

            image = Image(
                device_id=device.id,
                greenhouse_id=device.greenhouse_id,
                filename=filename,
                storage_url=storage_url,
                file_size=file_size,
                position_x=px,
                position_y=py,
                chassis_point="",
                greenhouse_point="",
                captured_at=datetime.now(timezone.utc),
                ai_analysis={"file_hash": file_hash},
                tags=["tcp"]
            )
            db.add(image)
            db.commit()
            print(f"[DB] ✅ 写入成功！前端马上能看到！")
    except Exception as e:
        print(f"[DB] 错误: {e}")

def handle_tcp_client(conn, addr):
    print(f"[TCP] 客户端连接: {addr}")
    try:
        header = b""
        while len(header) < HEADER_SIZE:
            packet = conn.recv(HEADER_SIZE - len(header))
            if not packet:
                return
            header += packet

        magic, ver, px, py, md5_bytes, fname_bytes = struct.unpack(HEADER_FORMAT, header)
        if magic != MAGIC:
            return

        original_filename = fname_bytes.decode("utf-8").strip("\x00")
        file_md5 = md5_bytes.decode("utf-8").strip("\x00")
        device_id = original_filename.split("_")[0]

        filename = f"{os.urandom(4).hex()}.jpg"
        dev_dir = ensure_dir(os.path.join("/opt/greenhouse/uploads", device_id))
        filepath = os.path.join(dev_dir, filename)

        with open(filepath, "wb") as f:
            while True:
                data = conn.recv(BUFFER_SIZE)
                if not data:
                    break
                f.write(data)

        print(f"[TCP] 文件已保存: {filepath}")
        save_to_db(device_id, filename, filepath, px, py, file_md5)

    except Exception as e:
        print(f"[TCP] 错误: {e}")
    finally:
        conn.close()

def start_tcp_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((TCP_HOST, TCP_PORT))
    s.listen(10)
    print(f"✅ TCP 图片服务启动: {TCP_HOST}:{TCP_PORT}")

    while True:
        conn, addr = s.accept()
        t = threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True)
        t.start()

if __name__ == "__main__":
    start_tcp_server()