"""T7：断流观测（连续 N 条间隔 > 2×周期 → 记一次事件，只记录不告警）。"""
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import outage_watch  # noqa: E402

T0 = datetime(2026, 10, 23, 8, 0, 0, tzinfo=timezone.utc)


def seq(*offsets):
    return [T0 + timedelta(seconds=o) for o in offsets]


def test_no_gap_no_event():
    times = seq(0, 60, 120, 180, 240)
    assert outage_watch.find_outages(times, 60, 3, now=T0 + timedelta(seconds=300)) == []


def test_single_long_gap_below_n_is_not_an_outage():
    """只有 1 条长间隔不算断流 —— 网络抖动/补传不该被记成事件。"""
    times = seq(0, 60, 900, 960, 1020)          # 中间一条 840s
    assert outage_watch.find_outages(times, 60, 3, now=T0 + timedelta(seconds=1080)) == []


def test_n_consecutive_gaps_is_an_outage():
    times = seq(0, 60, 900, 1800, 2700, 2760)   # 3 条 840s 间隔
    ev = outage_watch.find_outages(times, 60, 3, now=T0 + timedelta(seconds=2820))
    ended = [e for e in ev if e["kind"] == "ended"]
    assert len(ended) == 1
    assert ended[0]["gap_count"] == 3
    assert ended[0]["max_gap_s"] == 900.0   # 三段间隔 840/900/900，取最长
    assert ended[0]["threshold_s"] == 120.0


def test_ongoing_silence_is_detected_without_waiting_for_n():
    """设备停传时不会再有"下一条间隔"，所以必须靠 now - last 判定。"""
    times = seq(0, 60, 120)
    ev = outage_watch.find_outages(times, 60, 3, now=T0 + timedelta(seconds=3600))
    on = [e for e in ev if e["kind"] == "ongoing"]
    assert len(on) == 1
    assert on[0]["silent_s"] == 3480.0


def test_no_data_returns_no_event():
    assert outage_watch.find_outages([], 60, 3, now=T0) == []


def test_append_events_is_idempotent(tmp_path):
    p = str(tmp_path / "events.jsonl")
    ev = [{"kind": "ongoing", "started_at": "2026-10-23T08:54:18+00:00", "silent_s": 100}]
    assert outage_watch.append_events(p, ev, "JY001") == 1
    assert outage_watch.append_events(p, ev, "JY001") == 0       # 同一事件不重复记
    assert len(outage_watch.load_events(p)) == 1
    assert outage_watch.append_events(p, ev, "JY002") == 1       # 换设备算新事件


def test_bad_lines_in_events_file_are_skipped(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text("not-json\n" + json.dumps({"device": "JY001", "kind": "ended"}) + "\n",
                 encoding="utf-8")
    assert len(outage_watch.load_events(str(p))) == 1


@pytest.mark.asyncio
async def test_runs_against_test_database(iso, tmp_path):
    """真跑一次：测试库里造一段"正常 + 大缺口"的序列，脚本必须看出进行中断流。"""
    from app.core.database import AsyncSessionLocal
    from app.models.models import DeviceType, Telemetry

    fx, db = iso
    cid = await fx.company(db, "OW-C")
    gh = await fx.greenhouse(db, cid, "OW-GH")
    dev = await fx.device(db, gh, DeviceType.SENSOR, "OW-DEV")
    await db.flush()
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    for i, off in enumerate([0, 60, 120, 1800, 3600]):     # 前三条正常，之后两条大缺口
        tid = str(uuid.uuid4())
        # outage_watch 按 created_at（入库时刻）算间隔，必须显式给，
        # 否则 server_default now() 会把 5 条都写成"现在"，看不出缺口
        db.add(Telemetry(id=tid, device_id=dev.id, greenhouse_id=gh.id, company_id=cid,
                         timestamp=base + timedelta(seconds=off),
                         created_at=base + timedelta(seconds=off),
                         data_type="telemetry",
                         msg_id=f"OW/1/{i}", temperature=20.0))
        fx.tel_rows.append(tid)
    await db.commit()

    from app.core.config import settings
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    events_file = str(tmp_path / "e.jsonl")
    p = subprocess.run([sys.executable, "scripts/outage_watch.py", "--db-url", dsn,
                        "--device", dev.device_id, "--period", "60", "--n", "2",
                        "--lookback-hours", "6", "--events-file", events_file, "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["rows"] == 5
    kinds = {e["kind"] for e in out["events"]}
    assert "ended" in kinds, out          # 两段 1680/1800s 的大间隔（n=2）
    assert "ongoing" in kinds, out        # 最后一条距今 > 2×周期
    assert out["added"] >= 1
