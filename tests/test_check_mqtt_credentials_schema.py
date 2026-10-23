"""T6：006 前置列比对脚本的自测（测试库已跑过 006）。"""
import json
import os
import subprocess
import sys

SCRIPT = os.path.join("scripts", "check_mqtt_credentials_schema.py")
TEST_DB_MARKER = "greenhouse_nightly_test"


def sync_dsn() -> str:
    from app.core.config import settings
    url = settings.DATABASE_URL
    assert TEST_DB_MARKER in url
    return url.replace("postgresql+asyncpg://", "postgresql://")


def test_on_migrated_test_db_no_blocking_problem():
    """跑过 006 的库：不缺列、不缺索引、退出码 0。"""
    p = subprocess.run([sys.executable, SCRIPT, "--db-url", sync_dsn()],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "缺列          : 无" in p.stdout
    assert "006 待补列    : 无" in p.stdout
    assert "缺索引        : 无" in p.stdout


def test_json_mode_shape():
    p = subprocess.run([sys.executable, SCRIPT, "--db-url", sync_dsn(), "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0
    d = json.loads(p.stdout)
    assert d["db"] == TEST_DB_MARKER
    assert d["table_exists"] is True
    assert d["missing_columns"] == []
    assert "device_id" in d["columns_present"]
    assert isinstance(d["credentials"], list)


def test_missing_dsn_fails_fast():
    p = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k != "DATABASE_SYNC_URL"})
    assert p.returncode == 2
    assert "缺少 DSN" in p.stderr
