"""M4 业务模块契约测试。

覆盖：
  1. 种植批次：创建 / 状态流转 / 越权不可见
  2. 控制命令流转：待发送→已发送→已接收→执行中→成功，**ACK ≠ 执行结果**
  3. 指标定义最小集可用
  4. AI 受控：只吃后端给的只读快照，模块不碰 DB / MQTT，产出不得是控制动作
  5. 报表导出：限定公司范围（csv / json）
"""
import ast
import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import yaml
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.ai_advisor import (
    assert_no_control_action, generate_advice,
)
from app.core.command_state import is_acked, is_executed
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import AiAdviceLog, MetricDefinition, UserRole
from tests.conftest import auth
from tests.iso_fx import uid

API = settings.API_PREFIX
ROOT = Path(__file__).resolve().parent.parent
ROOT_DOCS = ROOT / "docs"


def _t(minutes=0, hours=0, days=0):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes, hours=hours,
                                                  days=days)


# ── 1. 种植批次 ──────────────────────────────────────────────
async def test_planting_batch_crud_and_flow(client, iso):
    fx, db = iso
    c = await fx.company(db, "M4A")
    gh = await fx.greenhouse(db, c, "M4A-gh")
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    r = await client.post(f"{API}/planting-batches", json={
        "code": "PB-2026-01", "greenhouse_id": gh.id, "zone": "A 区",
        "crop_type": "番茄", "variety": "普罗旺斯", "area": 1.5,
        "planted_at": _t(days=10).isoformat(),
    }, headers=auth(token))
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert r.json()["status"] == "planned"
    assert r.json()["crop_type"] == "番茄"

    # planned → harvested 非法
    bad = await client.post(f"{API}/planting-batches/{bid}/status",
                            json={"status": "harvested"}, headers=auth(token))
    assert bad.status_code == 400, bad.text
    assert "非法批次状态流转" in bad.json()["detail"]

    for st in ("active", "harvested", "closed"):
        resp = await client.post(f"{API}/planting-batches/{bid}/status",
                                 json={"status": st}, headers=auth(token))
        assert resp.status_code == 200, f"{st}: {resp.text}"
        assert resp.json()["status"] == st
    assert resp.json()["harvested_at"] is not None

    # 终态不能再流转
    assert (await client.post(f"{API}/planting-batches/{bid}/status",
                              json={"status": "active"},
                              headers=auth(token))).status_code == 400

    dup = await client.post(f"{API}/planting-batches", json={
        "code": "PB-2026-01", "greenhouse_id": gh.id, "crop_type": "番茄"},
        headers=auth(token))
    assert dup.status_code == 409, dup.text


async def test_planting_batches_are_company_scoped(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "M4B-A")
    c_b = await fx.company(db, "M4B-B")
    gh_a = await fx.greenhouse(db, c_a, "M4B-A-gh")
    gh_b = await fx.greenhouse(db, c_b, "M4B-B-gh")
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    await fx.planting_batch(db, gh_a, "A-批", company_id=c_a)
    await fx.planting_batch(db, gh_b, "B-批", company_id=c_b)
    await db.commit()

    ra = (await client.get(f"{API}/planting-batches", headers=auth(t_a))).json()
    rb = (await client.get(f"{API}/planting-batches", headers=auth(t_b))).json()
    assert [x["code"] for x in ra["items"]] == ["A-批"], ra
    assert [x["code"] for x in rb["items"]] == ["B-批"], rb

    # 越权查看别家批次 → 404
    other = (await client.get(f"{API}/planting-batches", headers=auth(t_b))
             ).json()["items"][0]["id"]
    got = await client.get(f"{API}/planting-batches/{other}", headers=auth(t_a))
    assert got.status_code == 404, got.text


# ── 2. 控制命令流转（ACK ≠ 执行结果） ────────────────────────
async def test_command_ack_is_not_execution_result(client, iso):
    fx, db = iso
    c = await fx.company(db, "M4C")
    gh = await fx.greenhouse(db, c, "M4C-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    created = await client.post(f"{API}/actuator-commands", json={
        "device_id": dev.id, "action": "open_valve",
        "params": {"duration": 30}, "desired_state": {"valve": "open"},
    }, headers=auth(token))
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert created.json()["status"] == "pending"
    assert created.json()["is_executed"] is False

    sent = await client.post(f"{API}/actuator-commands/{cid}/send",
                             headers=auth(token))
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent"

    ack = await client.post(f"{API}/actuator-commands/{cid}/ack",
                            headers=auth(token))
    assert ack.status_code == 200, ack.text
    body = ack.json()
    assert body["status"] == "accepted"
    assert body["is_acked"] is True
    assert body["is_executed"] is False, "ACK 绝不等于执行成功"
    assert body["is_finished"] is False
    assert "已收到 ≠ 执行成功" in body["reminder"]

    # accepted → executing → executed（执行中是必须的中间态）
    running = await client.post(f"{API}/actuator-commands/{cid}/result",
                                json={"status": "executing"}, headers=auth(token))
    assert running.status_code == 200, running.text
    assert running.json()["status"] == "executing"
    assert running.json()["is_executed"] is False, "执行中不等于成功"

    ok = await client.post(f"{API}/actuator-commands/{cid}/result",
                           json={"status": "executed",
                                 "actual_state": {"valve": "open"}},
                           headers=auth(token))
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "executed"
    assert ok.json()["is_executed"] is True
    assert ok.json()["completed_at"] is not None
    assert ok.json()["accepted_at"] is not None


async def test_command_transitions_are_validated(client, iso):
    fx, db = iso
    c = await fx.company(db, "M4D")
    gh = await fx.greenhouse(db, c, "M4D-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    # 未 sent 就想 ack（pending → accepted）→ 400
    cmd = await fx.command(db, dev, gh, status="pending")
    await db.commit()
    bad = await client.post(f"{API}/actuator-commands/{cmd.id}/ack",
                            headers=auth(token))
    assert bad.status_code == 400, bad.text
    assert "非法命令状态流转" in bad.json()["detail"]

    # 终态之后不能再流转
    done = await fx.command(db, dev, gh, status="executed")
    await db.commit()
    again = await client.post(f"{API}/actuator-commands/{done.id}/result",
                              json={"status": "failed"}, headers=auth(token))
    assert again.status_code == 400, again.text

    # 超时路径：sent → timeout
    t_cmd = await fx.command(db, dev, gh, status="sent")
    await db.commit()
    r = await client.post(f"{API}/actuator-commands/{t_cmd.id}/result",
                          json={"status": "timeout"}, headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "timeout"
    assert r.json()["is_executed"] is False


async def test_command_list_is_company_scoped(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "M4E-A")
    c_b = await fx.company(db, "M4E-B")
    gh_a = await fx.greenhouse(db, c_a, "M4E-A-gh")
    gh_b = await fx.greenhouse(db, c_b, "M4E-B-gh")
    dev_a = await fx.device(db, gh_a)
    dev_b = await fx.device(db, gh_b)
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    await fx.command(db, dev_a, gh_a, action="a-open")
    await fx.command(db, dev_b, gh_b, action="b-open")
    await db.commit()

    ra = (await client.get(f"{API}/actuator-commands", headers=auth(t_a))).json()
    rb = (await client.get(f"{API}/actuator-commands", headers=auth(t_b))).json()
    assert [x["action"] for x in ra["items"]] == ["a-open"], ra
    assert [x["action"] for x in rb["items"]] == ["b-open"], rb


async def test_command_state_machine_endpoint(client, iso):
    fx, db = iso
    u, token = await fx.user(db, UserRole.VIEWER)
    await db.commit()
    r = await client.get(f"{API}/actuator-commands/state-machine",
                         headers=auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success_status"] == "executed"
    assert body["transitions"]["pending"] == ["cancelled", "sent"]
    assert body["transitions"]["accepted"] == ["cancelled", "executing",
                                               "failed", "timeout"]


def test_ack_and_executed_helpers():
    assert is_acked("accepted") is True
    assert is_acked("executing") is True
    assert is_executed("accepted") is False
    assert is_executed("executing") is False
    assert is_executed("executed") is True


# ── 3. 指标定义 ──────────────────────────────────────────────
async def test_metric_definition_minimal_set_exists(client, iso):
    fx, db = iso
    u, token = await fx.user(db, UserRole.VIEWER)
    await db.commit()
    r = await client.get(f"{API}/metric-definitions", headers=auth(token))
    assert r.status_code == 200, r.text
    codes = {x["code"] for x in r.json()["items"]}
    for need in ("temperature", "humidity", "soil_moisture", "soil_temperature",
                 "soil_ec", "ph", "light", "co2"):
        assert need in codes, f"缺少指标定义：{need}"
    one = next(x for x in r.json()["items"] if x["code"] == "temperature")
    assert one["unit"] == "°C"
    assert one["min_value"] is not None and one["max_value"] is not None


async def test_metric_definition_create_and_conflict(client, iso):
    fx, db = iso
    u, token = await fx.user(db, UserRole.ADMIN)
    await db.commit()
    code = uid("M")
    r = await client.post(f"{API}/metric-definitions",
                          json={"code": code, "name": "测试指标", "unit": "x",
                                "min_value": 0, "max_value": 10},
                          headers=auth(token))
    assert r.status_code == 201, r.text
    dup = await client.post(f"{API}/metric-definitions",
                            json={"code": code, "name": "y"},
                            headers=auth(token))
    assert dup.status_code == 409, dup.text
    async with AsyncSessionLocal() as db2:
        row = (await db2.execute(select(MetricDefinition).where(
            MetricDefinition.code == code))).scalar_one_or_none()
        assert row is not None
        await db2.delete(row)
        await db2.commit()


# ── 4. AI 受控接口 ───────────────────────────────────────────
def test_ai_advisor_module_has_no_io_imports():
    """AI 侧拿不到 DB / MQTT：模块里不允许有任何 IO 相关的 import。"""
    import ast
    import app.core.ai_advisor as m
    tree = ast.parse(Path(m.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for forbidden in ("sqlalchemy", "sqlalchemy.ext.asyncio", "aiomqtt",
                      "redis", "asyncio"):
        for name in list(imported):
            assert not name.startswith(forbidden), f"ai_advisor 不应 import {name}"
    for forbidden in ("get_db", "AsyncSessionLocal", "mqtt_service"):
        assert forbidden not in imported, f"ai_advisor 不应引入 {forbidden}"


def test_ai_advisor_refuses_control_advice():
    with pytest.raises(ValueError):
        assert_no_control_action([{"kind": "control", "message": "打开风机"}])
    with pytest.raises(ValueError):
        assert_no_control_action([{"kind": "command"}])
    assert_no_control_action([{"kind": "risk"}, {"kind": "observation"}])


def test_ai_advisor_uses_only_snapshot():
    result = generate_advice({
        "greenhouse_id": "gh1",
        "metrics": {"temperature": 45.0, "humidity": 50.0},
        "thresholds": {"temperature": {"min": 5, "max": 35, "unit": "°C"},
                       "humidity": {"min": 30, "max": 80, "unit": "%"}},
        "as_of": "now",
    })
    kinds = {a["kind"] for a in result["advice"]}
    assert "risk" in kinds
    assert "control" not in kinds
    assert result["limits"]["db_access"] is False
    assert result["limits"]["mqtt_control"] is False
    # 只有 temperature 越界
    risky = [a for a in result["advice"] if a["kind"] == "risk"]
    assert [a["metric"] for a in risky] == ["temperature"]

    clean = generate_advice({"metrics": {"temperature": 25.0},
                             "thresholds": {"temperature": {"min": 5, "max": 35}}})
    assert clean["advice"][0]["kind"] == "observation"


async def test_ai_advice_endpoint_is_controlled(client, iso):
    fx, db = iso
    c = await fx.company(db, "M4F")
    gh = await fx.greenhouse(db, c, "M4F-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await fx.raw_telemetry(db, gh, dev, timestamp=_t(minutes=2),
                           created_at=_t(minutes=2), data_type="telemetry",
                           temperature=45.0, humidity=50.0, time_quality=1,
                           sampled_at=_t(minutes=2), company_id=c)
    await db.commit()

    r = await client.post(f"{API}/ai/advice",
                          json={"greenhouse_id": gh.id, "hours": 1},
                          headers=auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["limits"]["db_access"] is False
    assert body["limits"]["mqtt_control"] is False
    kinds = {a["kind"] for a in body["advice"]}
    assert "control" not in kinds
    assert any(a["kind"] == "risk" for a in body["advice"]), body["advice"]

    # 留痕：明确记录没碰数据库、没发控制
    async with AsyncSessionLocal() as db2:
        log = (await db2.execute(select(AiAdviceLog).where(
            AiAdviceLog.id == body["log_id"]))).scalar_one()
        assert log.db_access_used is False
        assert log.control_issued is False


async def test_ai_advice_cross_company_plant_is_404(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "M4G-A")
    c_b = await fx.company(db, "M4G-B")
    gh_b = await fx.greenhouse(db, c_b, "M4G-B-gh")
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    await db.commit()
    r = await client.post(f"{API}/ai/advice", json={"greenhouse_id": gh_b.id},
                          headers=auth(t_a))
    assert r.status_code == 404, r.text


async def test_ai_capabilities_declares_limits(client, iso):
    fx, db = iso
    u, token = await fx.user(db, UserRole.VIEWER)
    await db.commit()
    r = await client.get(f"{API}/ai/capabilities", headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["can_read_database_directly"] is False
    assert r.json()["can_publish_mqtt"] is False


# ── 5. 报表导出 ──────────────────────────────────────────────
async def test_report_export_is_company_scoped(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "M4H-A")
    c_b = await fx.company(db, "M4H-B")
    gh_a = await fx.greenhouse(db, c_a, "M4H-A-gh")
    gh_b = await fx.greenhouse(db, c_b, "M4H-B-gh")
    dev_a = await fx.device(db, gh_a)
    dev_b = await fx.device(db, gh_b)
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    await fx.raw_telemetry(db, gh_a, dev_a, timestamp=_t(minutes=5),
                           created_at=_t(minutes=5), temperature=21.0,
                           data_type="telemetry", company_id=c_a)
    await fx.raw_telemetry(db, gh_b, dev_b, timestamp=_t(minutes=5),
                           created_at=_t(minutes=5), temperature=22.0,
                           data_type="telemetry", company_id=c_b)
    await db.commit()

    ra = (await client.get(f"{API}/reports/telemetry?fmt=json",
                           headers=auth(t_a))).json()
    rb = (await client.get(f"{API}/reports/telemetry?fmt=json",
                           headers=auth(t_b))).json()
    assert ra["meta"]["count"] == 1, ra["meta"]
    assert ra["items"][0]["temperature"] == 21.0
    assert rb["items"][0]["temperature"] == 22.0

    # 别家大棚直接指定 → 404
    r = await client.get(f"{API}/reports/telemetry?greenhouse_id={gh_b.id}",
                         headers=auth(t_a))
    assert r.status_code == 404, r.text

    # 业务用户传 company_id 越权 → 403
    r2 = await client.get(f"{API}/reports/telemetry?company_id={c_b}",
                          headers=auth(t_a))
    assert r2.status_code == 403, r2.text


async def test_report_export_csv(client, iso):
    fx, db = iso
    c = await fx.company(db, "M4I")
    gh = await fx.greenhouse(db, c, "M4I-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await fx.raw_telemetry(db, gh, dev, timestamp=_t(minutes=5),
                           created_at=_t(minutes=5), temperature=21.0,
                           humidity=60.0, data_type="telemetry", company_id=c)
    await db.commit()

    r = await client.get(f"{API}/reports/telemetry?fmt=csv", headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 1, rows
    assert rows[0]["temperature"] == "21.0"
    assert rows[0]["humidity"] == "60.0"


async def test_report_summary_scoped(client, iso):
    fx, db = iso
    c = await fx.company(db, "M4J")
    gh = await fx.greenhouse(db, c, "M4J-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await fx.raw_telemetry(db, gh, dev, timestamp=_t(minutes=5),
                           created_at=_t(minutes=5), temperature=20.0,
                           humidity=50.0, data_type="telemetry", company_id=c)
    await fx.raw_telemetry(db, gh, dev, timestamp=_t(minutes=6),
                           created_at=_t(minutes=6), temperature=30.0,
                           humidity=70.0, data_type="telemetry", company_id=c)
    await db.commit()

    r = await client.get(f"{API}/reports/telemetry/summary?hours=24",
                         headers=auth(token))
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["count"] == 2
    assert item["avg_temperature"] == 25.0
    assert item["avg_humidity"] == 60.0


# ── 6. 契约文件 ──────────────────────────────────────────────
def test_contract_docs_exist():
    base = ROOT_DOCS
    for name in ("telemetry-v1.md", "state-machines.md",
                 "robot-integration-v1.md", "acceptance.md", "openapi.yaml"):
        f = base / name
        assert f.exists(), f"缺少契约文件：{name}"
        assert f.stat().st_size > 200, f"契约文件为空：{name}"


def test_openapi_export_matches_app():
    """docs/openapi.yaml 必须能由当前应用真实导出，且与 /openapi.json 一致。"""
    out = subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--stdout"],
        capture_output=True, text=True, cwd=ROOT, timeout=180)
    assert out.returncode == 0, out.stderr
    spec = yaml.safe_load(out.stdout)
    assert spec["openapi"].startswith("3.")
    paths = spec["paths"]
    for must in ("/api/v1/photo-catalog",
                 "/api/v1/photo-catalog/options",
                 "/api/v1/point-config/maps/{greenhouse_id}/publish",
                 "/api/v1/maintenance/recover",
                 "/api/v1/actuator-commands",
                 "/api/v1/ai/advice",
                 "/api/v1/planting-batches",
                 "/api/v1/reports/telemetry",
                 "/api/v1/freshness/devices",
                 "/api/v1/incidents"):
        assert must in paths, f"openapi 缺少路径：{must}"

    # 与已提交的 docs/openapi.yaml 保持一致
    committed = yaml.safe_load((ROOT_DOCS / "openapi.yaml").read_text(encoding="utf-8"))
    assert set(committed["paths"]) == set(paths)
