"""断流观测三态：正常运行 / 意外断流 / 计划停机。

原二态口径的用例（find_outages）全部保留 —— 那一层逻辑没动。
新增用例只覆盖"分类层"和"落盘去重"，这两处是三态改造的真正风险点。
"""
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


def wins(start=T0, end=None, device="JY001", **kw):
    w = {
        "id": kw.get("id", "mw-test"),
        "device": device,
        "operator": kw.get("operator", "现场-张工"),
        "reason": kw.get("reason", "计划断电检修"),
        "note": kw.get("note", ""),
        "start": start,
        "end": end if end is not None else start + timedelta(hours=2),
        "created_at": "",
    }
    return [w]


def ev(start, end=None, kind=None, **kw):
    """构造一个断流事件（不经 find_outages，聚焦分类层）。"""
    k = kind or ("ongoing" if end is None else "ended")
    e = {"kind": k, "started_at": start.isoformat(),
         "threshold_s": kw.get("threshold_s", 120.0)}
    if end is None:
        e["silent_s"] = kw.get("silent_s", 3600.0)
        e["ended_at"] = None
    else:
        e["ended_at"] = end.isoformat()
        e["gap_count"] = kw.get("gap_count", 3)
        e["max_gap_s"] = kw.get("max_gap_s", 900.0)
    return e


# ─────────────────────── 原有二态用例（必须继续通过）───────────────────────
def test_no_gap_no_event():
    times = seq(0, 60, 120, 180, 240)
    assert outage_watch.find_outages(times, 60, 3, now=T0 + timedelta(seconds=300)) == []


def test_single_long_gap_below_n_is_not_an_outage():
    times = seq(0, 60, 900, 960, 1020)
    assert outage_watch.find_outages(times, 60, 3, now=T0 + timedelta(seconds=1080)) == []


def test_n_consecutive_gaps_is_an_outage():
    times = seq(0, 60, 900, 1800, 2700, 2760)
    evs = outage_watch.find_outages(times, 60, 3, now=T0 + timedelta(seconds=2820))
    ended = [e for e in evs if e["kind"] == "ended"]
    assert len(ended) == 1
    assert ended[0]["gap_count"] == 3
    assert ended[0]["max_gap_s"] == 900.0
    assert ended[0]["threshold_s"] == 120.0


def test_ongoing_silence_is_detected_without_waiting_for_n():
    times = seq(0, 60, 120)
    evs = outage_watch.find_outages(times, 60, 3, now=T0 + timedelta(seconds=3600))
    on = [e for e in evs if e["kind"] == "ongoing"]
    assert len(on) == 1
    assert on[0]["silent_s"] == 3480.0


def test_no_data_returns_no_event():
    assert outage_watch.find_outages([], 60, 3, now=T0) == []


# ───────── 回看窗口内 0 条样本：必须靠全表末条(anchor)判，不能退化成"正常" ─────────
# 缺陷背景（2026-10-25 夜间实测）：JY001 末条遥测 2026-10-23 08:54，静默 62h，
# 超出默认 48h 回看窗口 ⇒ 窗口内 0 条 ⇒ 原实现无任何事件 ⇒ 顶层状态退回 ok，
# 打印出「当前状态：正常运行」。断得越久越像正常，方向完全反了。
def test_empty_window_with_anchor_detects_ongoing_silence():
    anchor = T0 - timedelta(hours=62)          # 全表末条，远在回看窗口之外
    evs = outage_watch.find_outages([], 60, 3, now=T0, anchor=anchor)
    on = [e for e in evs if e["kind"] == "ongoing"]
    assert len(on) == 1
    assert on[0]["started_at"] == anchor.isoformat()
    assert on[0]["silent_s"] == round(62 * 3600, 1)


def test_empty_window_without_anchor_fabricates_nothing():
    """全表一条都没有：不编造起点，也不编造时长。"""
    assert outage_watch.find_outages([], 60, 3, now=T0, anchor=None) == []


def test_empty_window_with_anchor_and_no_window_is_outage():
    """窗口内 0 条 + 无计划停机 ⇒ 意外断流，且计入断流次数、需要通知。"""
    anchor = T0 - timedelta(hours=62)
    raw = outage_watch.find_outages([], 60, 3, now=T0, anchor=anchor)
    c = outage_watch.classify(raw, [], "JY001", T0)
    assert c[0]["state"] == "outage"
    assert c[0]["counts_as_outage"] is True
    assert c[0]["should_notify"] is True


def test_empty_window_with_anchor_inside_maintenance_is_maintenance():
    """JY001 当前真实场景：静默 62h，但仍在维护窗口内 ⇒ 计划停机，不通知。"""
    anchor = T0 - timedelta(hours=62)
    wins = [{"id": "mw-1", "device": "JY001", "operator": "用户",
             "reason": "主动断电", "note": "",
             "start": anchor - timedelta(minutes=1),
             "end": T0 + timedelta(hours=19)}]
    raw = outage_watch.find_outages([], 60, 3, now=T0, anchor=anchor)
    c = outage_watch.classify(raw, wins, "JY001", T0)
    assert c[0]["state"] == "maintenance"
    assert c[0]["should_notify"] is False
    s = outage_watch.summarize("JY001", [], c, wins, 60, T0, anchor=anchor)
    assert s["state"] == "maintenance"          # ← 修之前这里是 ok
    assert s["device_offline"] is True
    assert s["data_stale"] is True


def test_empty_window_after_maintenance_end_escalates_to_overrun():
    """维护窗口已过期仍无数据 ⇒ 计划停机超期，必须通知（这就是 10-26 18:00 之后）。"""
    anchor = T0 - timedelta(hours=62)
    wins = [{"id": "mw-1", "device": "JY001", "operator": "用户",
             "reason": "主动断电", "note": "",
             "start": anchor - timedelta(minutes=1),
             "end": T0 - timedelta(hours=1)}]   # 窗口 1 小时前就到期了
    raw = outage_watch.find_outages([], 60, 3, now=T0, anchor=anchor)
    c = outage_watch.classify(raw, wins, "JY001", T0)
    assert c[0]["state"] == "maintenance_overrun"
    assert c[0]["should_notify"] is True
    assert c[0]["counts_as_outage"] is False    # 超期不是设备故障，不进断流次数
    s = outage_watch.summarize("JY001", [], c, wins, 60, T0, anchor=anchor)
    assert s["state"] == "maintenance_overrun"


def test_summary_reports_silence_from_anchor_when_window_empty():
    """窗口内 0 条时，last_seen / 静默时长仍要如实报出来（不写 None 糊过去）。"""
    anchor = T0 - timedelta(hours=62)
    s = outage_watch.summarize("JY001", [], [], [], 60, T0, anchor=anchor)
    assert s["last_seen"] == anchor.isoformat()
    assert s["silent_seconds"] == round(62 * 3600, 1)
    assert s["device_offline"] is True


# ─────────────────────── 三态分类 ───────────────────────
def test_no_window_is_unexpected_outage():
    now = T0 + timedelta(seconds=3600)
    raw = outage_watch.find_outages(seq(0, 60, 120), 60, 3, now=now)
    c = outage_watch.classify(raw, [], "JY001", now)
    assert c[0]["state"] == "outage"
    assert c[0]["should_notify"] is True
    assert c[0]["counts_as_outage"] is True
    assert c[0]["window"] is None


def test_window_covers_ongoing_is_planned_maintenance():
    """计划停机：不通知，但事件仍然被记下来（事后要能查）。"""
    now = T0 + timedelta(minutes=60)
    raw = outage_watch.find_outages(seq(0, 60, 120), 60, 3, now=now)
    c = outage_watch.classify(raw, wins(end=T0 + timedelta(hours=3)), "JY001", now)
    assert c[0]["state"] == "maintenance"
    assert c[0]["should_notify"] is False
    assert c[0]["counts_as_outage"] is False
    assert c[0]["window"]["operator"] == "现场-张工"
    assert c[0]["window"]["reason"] == "计划断电检修"
    assert c[0]["window"]["expired"] is False


def test_window_expired_escalates_to_overrun():
    """到期自动恢复监测：窗口 end 一过，同一次静默必须变成要通知。"""
    now = T0 + timedelta(hours=2)
    raw = outage_watch.find_outages(seq(0, 60, 120), 60, 3, now=now)
    c = outage_watch.classify(raw, wins(end=T0 + timedelta(minutes=30)), "JY001", now)
    assert c[0]["state"] == "maintenance_overrun"
    assert c[0]["should_notify"] is True
    # 超期不是"设备故障"：不进断流次数，否则计划停机还是会污染统计
    assert c[0]["counts_as_outage"] is False
    assert c[0]["overdue_s"] == 5400.0


def test_ended_inside_window_is_maintenance():
    now = T0 + timedelta(hours=5)
    e = ev(T0 + timedelta(hours=1), end=T0 + timedelta(hours=2))
    c = outage_watch.classify([e], wins(end=T0 + timedelta(hours=3)), "JY001", now)
    assert c[0]["state"] == "maintenance"


def test_ended_after_window_end_is_overrun():
    now = T0 + timedelta(hours=5)
    e = ev(T0 + timedelta(hours=1), end=T0 + timedelta(hours=4))
    c = outage_watch.classify([e], wins(end=T0 + timedelta(hours=3)), "JY001", now)
    assert c[0]["state"] == "maintenance_overrun"
    assert c[0]["window"]["expired"] is True


def test_new_silence_after_window_end_returns_to_outage():
    """窗口结束之后新发生的静默，必须是意外断流（到期自动恢复监测）。"""
    now = T0 + timedelta(hours=6)
    e = ev(T0 + timedelta(hours=4))          # 起点在窗口 end 之后
    c = outage_watch.classify([e], wins(end=T0 + timedelta(hours=1)), "JY001", now)
    assert c[0]["state"] == "outage"
    assert c[0]["should_notify"] is True


def test_window_uses_intersection_not_containment():
    """断电时刻未知时窗口起点是估计值，事件起点略早也必须算计划停机。"""
    now = T0 + timedelta(minutes=60)
    e = ev(T0)                               # 事件从 T0 开始
    w = wins(start=T0 + timedelta(seconds=90), end=T0 + timedelta(hours=2))
    c = outage_watch.classify([e], w, "JY001", now)
    assert c[0]["state"] == "maintenance"


def test_other_device_window_does_not_apply():
    now = T0 + timedelta(minutes=60)
    e = ev(T0)
    c = outage_watch.classify([e], wins(device="JY002", end=T0 + timedelta(hours=2)),
                              "JY001", now)
    assert c[0]["state"] == "outage"


# ─────────────────────── 汇总：页面要的那两个布尔量 ───────────────────────
def test_maintenance_still_reports_device_offline_and_stale():
    """用户明确要求：计划停机也要显示「设备离线、数据已过期」，只是不告警。"""
    now = T0 + timedelta(hours=1)
    times = seq(0, 60, 120)                  # 最后一条在 T0+120
    raw = outage_watch.find_outages(times, 60, 3, now=now)
    c = outage_watch.classify(raw, wins(end=T0 + timedelta(hours=3)), "JY001", now)
    s = outage_watch.summarize("JY001", times, c, wins(end=T0 + timedelta(hours=3)),
                               60, now)
    assert s["state"] == "maintenance"
    assert s["device_offline"] is True
    assert s["data_stale"] is True
    assert s["should_notify"] is False
    assert s["in_maintenance_window"] is True
    assert s["maintenance"]["operator"] == "现场-张工"
    assert s["outage_count"] == 0


def test_summary_ok_when_silence_already_recovered():
    now = T0 + timedelta(hours=1)
    times = seq(0, 60, 120, 3500, 3560)      # 恢复过了，现在有数据
    raw = outage_watch.find_outages(times, 60, 3, now=now)
    s = outage_watch.summarize("JY001", times, raw, [], 60, now)
    assert s["state"] == "ok"
    assert s["should_notify"] is False
    assert s["device_offline"] is False


def test_summary_no_data_is_offline_but_not_fabricated():
    """完全没有数据：判离线，但不编造 last_seen，也不凭空算断流时长。"""
    now = T0
    s = outage_watch.summarize("JY001", [], [], [], 60, now)
    assert s["device_offline"] is True
    assert s["data_stale"] is True
    assert s["last_seen"] is None
    assert s["silent_seconds"] is None
    assert s["state"] == "ok"               # 无事件可判，不虚构断流


# ─────────────────────── 落盘与容错 ───────────────────────
def test_events_dedupe_is_idempotent(tmp_path):
    p = str(tmp_path / "events.jsonl")
    e = {"kind": "ongoing", "started_at": "2026-10-23T08:54:18+00:00",
         "state": "outage", "silent_s": 100}
    assert outage_watch.append_events(p, [e], "JY001") == 1
    assert outage_watch.append_events(p, [e], "JY001") == 0
    assert len(outage_watch.load_events(p)) == 1
    assert outage_watch.append_events(p, [e], "JY002") == 1


def test_state_evolution_is_kept_as_new_record(tmp_path):
    """同一次静默从 maintenance 演进到 maintenance_overrun 要留痕，不能算重复。"""
    p = str(tmp_path / "events.jsonl")
    base = {"kind": "ongoing", "started_at": "2026-10-23T08:54:18+00:00", "silent_s": 100}
    assert outage_watch.append_events(p, [dict(base, state="maintenance")], "JY001") == 1
    assert outage_watch.append_events(p, [dict(base, state="maintenance")], "JY001") == 0
    assert outage_watch.append_events(p, [dict(base, state="maintenance_overrun")],
                                      "JY001") == 1
    states = [r["state"] for r in outage_watch.load_events(p)]
    assert states == ["maintenance", "maintenance_overrun"]


def test_bad_lines_in_events_file_are_skipped(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text("not-json\n" + json.dumps({"device": "JY001", "kind": "ended"}) + "\n",
                 encoding="utf-8")
    assert len(outage_watch.load_events(str(p))) == 1


def test_missing_window_file_means_no_planned_downtime(tmp_path):
    assert outage_watch.load_windows(str(tmp_path / "nope.json")) == []


def test_broken_window_file_does_not_stop_monitoring(tmp_path):
    """窗口文件坏了必须降级成"无计划停机"，不能让监测整体停摆。"""
    p = tmp_path / "mw.json"
    p.write_text("{ this is not json", encoding="utf-8")
    assert outage_watch.load_windows(str(p)) == []


def test_window_without_device_is_skipped(tmp_path):
    p = tmp_path / "mw.json"
    p.write_text(json.dumps({"windows": [
        {"device": "", "start": "2026-10-23T08:00:00+00:00", "end": "2026-10-23T10:00:00+00:00"},
        {"device": "JY001", "start": "2026-10-23T08:00:00+00:00",
         "end": "2026-10-23T10:00:00+00:00"},
    ]}), encoding="utf-8")
    w = outage_watch.load_windows(str(p))
    assert len(w) == 1 and w[0]["device"] == "JY001"


def test_cli_add_maintenance_roundtrip(tmp_path):
    """登记计划停机：操作人/原因/起止时间必须真的落到文件里。"""
    p = str(tmp_path / "mw.json")
    r = subprocess.run([sys.executable, "scripts/outage_watch.py",
                        "--add-maintenance", "JY001",
                        "--operator", "现场-张工",
                        "--reason", "更换供电适配器",
                        "--start", "2026-10-23T08:54:18+08:00",
                        "--end", "2026-10-25T18:00:00+08:00",
                        "--note", "断电时刻未知，取最后一条遥测时刻",
                        "--maintenance-file", p],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    w = outage_watch.load_windows(p)
    assert len(w) == 1
    assert w[0]["operator"] == "现场-张工"
    assert w[0]["reason"] == "更换供电适配器"
    assert w[0]["note"].startswith("断电时刻未知")
    assert w[0]["start"] < w[0]["end"]


def test_cli_add_maintenance_rejects_reversed_window(tmp_path):
    p = str(tmp_path / "mw.json")
    r = subprocess.run([sys.executable, "scripts/outage_watch.py",
                        "--add-maintenance", "JY001",
                        "--start", "2026-10-25T18:00:00+08:00",
                        "--end", "2026-10-24T18:00:00+08:00",
                        "--maintenance-file", p],
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert not os.path.exists(p)


# ─────────────────────── 真库（保留原有用例）───────────────────────
@pytest.mark.asyncio
async def test_runs_against_test_database(iso, tmp_path):
    """真跑一次：测试库里造一段"正常 + 大缺口"的序列，脚本必须看出进行中断流。"""
    from app.core.database import AsyncSessionLocal  # noqa: F401
    from app.models.models import DeviceType, Telemetry

    fx, db = iso
    cid = await fx.company(db, "OW-C")
    gh = await fx.greenhouse(db, cid, "OW-GH")
    dev = await fx.device(db, gh, DeviceType.SENSOR, "OW-DEV")
    await db.flush()
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    for i, off in enumerate([0, 60, 120, 1800, 3600]):
        tid = str(uuid.uuid4())
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
                        "--lookback-hours", "6", "--events-file", events_file,
                        "--maintenance-file", str(tmp_path / "mw.json"), "--json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["rows"] == 5
    kinds = {e["kind"] for e in out["events"]}
    assert "ended" in kinds, out
    assert "ongoing" in kinds, out
    assert out["added"] >= 1
    # 没登记计划停机 → 必须判成意外断流，且要通知
    assert out["state"] == "outage"
    assert out["should_notify"] is True
    assert out["device_offline"] is True
