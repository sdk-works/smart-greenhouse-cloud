import socket
import struct
import os
import hashlib
import threading
import re
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
MAX_FILE_SIZE = 20 * 1024 * 1024
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

def parse_captured_at(filename):
    """Read the capture epoch written by the robot at the end of the filename."""
    match = re.search(r"_(\d{10})(?:\.\d+)?\.(?:jpe?g|png)$", filename, re.IGNORECASE)
    if not match:
        return None
    try:
        captured_at = datetime.fromtimestamp(int(match.group(1)), timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    # Reject obviously invalid device clocks instead of storing misleading dates.
    now = datetime.now(timezone.utc)
    if captured_at.year < 2020 or captured_at > now.replace(year=now.year + 1):
        return None
    return captured_at

# 同步写入数据库，绝对稳定
def save_to_db(device_id, filename, storage_name, filepath, px, py, file_hash, chassis_point):
    print(f"[DB] 写入设备: {device_id}, 文件: {filename}")
    try:
        with Session(engine) as db:
            device = db.query(Device).filter(Device.device_id == device_id).first()
            if not device:
                print(f"[DB] 设备不存在: {device_id}")
                return False

            existing = db.query(Image).filter(
                Image.device_id == device.id,
                Image.ai_analysis["file_hash"].as_string() == file_hash,
            ).first()
            if existing:
                print(f"[DB] 重复图片，沿用已有记录: {existing.id}")
                return "duplicate"

            file_size = os.path.getsize(filepath)
            storage_url = f"/uploads/{device_id}/{storage_name}"
            received_at = datetime.now(timezone.utc)
            captured_at = parse_captured_at(filename) or received_at

            image = Image(
                device_id=device.id,
                greenhouse_id=device.greenhouse_id,
                filename=filename,
                storage_url=storage_url,
                file_size=file_size,
                position_x=px,
                position_y=py,
                chassis_point=chassis_point,
                greenhouse_point="",
                captured_at=captured_at,
                ai_analysis={
                    "file_hash": file_hash,
                    "uploaded_at": received_at.isoformat(),
                    "capture_time_source": "filename" if captured_at != received_at else "server_fallback",
                },
                tags=["tcp"]
            )
            db.add(image)
            db.commit()
            print(f"[DB] ✅ 写入成功！前端马上能看到！")
            return True
    except Exception as e:
        print(f"[DB] 错误: {e}")
        return False

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

        safe_original = os.path.basename(original_filename)
        display_filename = safe_original[len(device_id) + 1:] if safe_original.startswith(device_id + "_") else safe_original
        ext = os.path.splitext(display_filename)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png"):
            ext = ".jpg"
        storage_name = f"{os.urandom(8).hex()}{ext}"
        dev_dir = ensure_dir(os.path.join("/opt/greenhouse/uploads", device_id))
        filepath = os.path.join(dev_dir, storage_name)

        received = 0
        digest = hashlib.md5()
        conn.settimeout(30)
        with open(filepath, "wb") as f:
            while True:
                data = conn.recv(BUFFER_SIZE)
                if not data:
                    break
                received += len(data)
                if received > MAX_FILE_SIZE:
                    raise ValueError("file exceeds 20MB limit")
                digest.update(data)
                f.write(data)

        print(f"[TCP] 文件已保存: {filepath}")
        if digest.hexdigest().lower() != file_md5.lower():
            raise ValueError("MD5 mismatch")
        parts = display_filename.split("-")
        chassis_point = parts[1] if len(parts) > 2 and parts[0] == "nav" else ""
        db_result = save_to_db(device_id, display_filename, storage_name, filepath,
                               px, py, file_md5, chassis_point)
        if not db_result:
            raise ValueError("database insert failed")
        if db_result == "duplicate" and os.path.exists(filepath):
            os.remove(filepath)
        conn.sendall(b"OK\n")

    except Exception as e:
        print(f"[TCP] 错误: {e}")
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
            conn.sendall(("ERR " + str(e)[:120] + "\n").encode("utf-8"))
        except Exception:
            pass
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
