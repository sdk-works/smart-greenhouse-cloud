#!/usr/bin/env python3
"""断流观测（三态）：正常运行 / 意外断流 / 计划停机。

**只记录，不告警到人**。落盘是一行一个 JSON 的本地文件（默认
`/opt/greenhouse-nightly/logs/outage-events.jsonl`），不写业务库、不发消息、
不动设备。用途是让"设备什么时候开始不传了"变成可查的记录，而不是靠人发现。

## 三态口径（2026-10-24 起）

| state | 含义 | 页面 | 计入断流次数 | 告警 |
|---|---|---|---|---|
| `ok` | 数据新鲜，或断流已恢复 | 正常 | 否 | 否 |
| `maintenance` | 计划停机窗口内的静默 | **仍显示「设备离线、数据已过期」** | 否 | **否（暂停）** |
| `outage` | 窗口外的静默 | 设备离线、数据已过期 | 是 | 是 |
| `maintenance_overrun` | 计划停机超期仍未恢复 | 设备离线、数据已过期 | 否（不是设备故障） | **是**（维护超期） |

`maintenance_overrun` 是"到期自动恢复监测"的落点：窗口 `end` 一过，
同一次静默立刻升级为需要通知的状态，不需要人工去关窗口。它不算"设备故障"，
所以不进断流次数；但因为"该回来了没回来"，必须报出来。

**为什么必须分三态**：2026-10-23 JY001 那次是用户主动断电。若按二态口径，
它会被记成"一次设备故障"，并且会污染"漏报率"——那是把计划停机当成故障统计。
口径错了，后面所有可靠性数字都不可信。

## 判据（都按 `created_at` = 入库时刻算，设备侧没有可信采集时间）
  1. 已结束的断流：连续 >= N 条相邻间隔 > 2×period
  2. 进行中的断流：`now - 最后一条` > 2×period（不必等够 N 条）
     —— 这一条正是 2026-10-23 08:55 那次的情况：设备上行停了，
        而 EMQX 因为 keepalive=0 一直显示 connected
        （见 docs/outage-triage-20261023.md §3）。

## 「最后一条」必须取全表，不能只看回看窗口（2026-10-25 夜间修的缺陷）

原实现只看 `--lookback-hours`（默认 48h）窗口内的样本：窗口内一条都没有时
`find_outages` 返回空 ⇒ `summarize` 没有 ongoing 事件 ⇒ 顶层状态退回 `ok`。
**于是设备离线越久、状态看起来越"正常"** —— 断的方向完全反了。
2026-10-25 23:35 实测就是这个样子：JY001 末条遥测 2026-10-23 08:54（静默 62h，
已超出 48h 窗口），脚本却打印「当前状态：正常运行」，只是靠下面那行维护窗口
注解才让人看出不对。

修法：另外查一次**该设备的全表末条**作为锚点 `anchor`：
  * 窗口内有样本 → `anchor` 就是窗口内末条（行为与原来完全一致）；
  * 窗口内 0 条、但全表有末条 → 用它判"进行中的断流"，静默起点如实取该时刻；
  * 全表一条都没有 → `anchor=None`，**不编造** `last_seen` / 断流时长，仍是 `ok`。

## 维护窗口（计划停机）怎么登记

窗口文件是 JSON，可手改，也可用 CLI：

  python3 scripts/outage_watch.py --add-maintenance JY001 \\
      --operator "现场-张工" --reason "计划断电检修" \\
      --start "2026-10-23T08:54:18+08:00" --end "2026-10-25T18:00:00+08:00" \\
      --note "断电时刻未知，取最后一条遥测时刻"

判定用**区间相交**，不要求包含：这样窗口起点比实际断电时刻稍晚也不会漏判。
窗口 `end` 到了不会自动删除，但**不再覆盖**之后新发生的静默——这就是"到期自动恢复监测"。

## 用法

  python3 scripts/outage_watch.py --db-url "$DATABASE_SYNC_URL" --device JY001
  python3 scripts/outage_watch.py --db-url "$DATABASE_SYNC_URL" --device JY001 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2

DEFAULT_EVENTS = "/opt/greenhouse-nightly/logs/outage-events.jsonl"
DEFAULT_MAINTENANCE = "/opt/greenhouse-nightly/logs/maintenance.json"

STATE_OK = "ok"
STATE_MAINTENANCE = "maintenance"
STATE_OUTAGE = "outage"
STATE_OVERRUN = "maintenance_overrun"

# 哪些状态要通知到人
NOTIFY_STATES = {STATE_OUTAGE, STATE_OVERRUN}
# 哪些状态算"一次设备断流"（进统计分母）
COUNT_AS_OUTAGE = {STATE_OUTAGE}

# device_id 可能是 devices.id（uuid）也可能是 devices.device_id（'JY001'），两种都认
SQL = """
SELECT t.created_at
  FROM telemetry t
  JOIN devices d ON d.id = t.device_id
 WHERE (d.device_id = %(dev)s OR d.id = %(dev)s)
   AND t.created_at >= now() - interval '%(hours)s hours'
 ORDER BY t.created_at
""".replace("%(dev)s", "%%(dev)s")   # 保留 psycopg2 的命名参数，只让 hours 走 % 格式化

# 全表末条：回看窗口内一条都没有时，用它当"进行中静默"的起点。
# 没有它，设备离线超过回看窗口后会退化成"正常运行"（见文件头说明）。
SQL_LAST = """
SELECT max(t.created_at)
  FROM telemetry t
  JOIN devices d ON d.id = t.device_id
 WHERE (d.device_id = %(dev)s OR d.id = %(dev)s)
"""


# ────────────────────────────── 时间与窗口 ──────────────────────────────
def parse_dt(s):
    """解析 ISO 时间。带时区的直接用；不带时区的按服务器本地时区解释。

    `Z` 结尾在 Python 3.10 的 fromisoformat 里不认，先替换掉。
    """
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=datetime.now().astimezone().tzinfo)
    txt = str(s).strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    dt = datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def load_windows(path):
    """读维护窗口。文件不存在 / 坏掉都当"没有计划停机"，不阻断监测。"""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:                      # 坏文件不能让监测停摆
        print(f"[warn] 维护窗口文件无法解析，按「无计划停机」处理：{e}", file=sys.stderr)
        return []
    wins = doc.get("windows") if isinstance(doc, dict) else doc
    if not isinstance(wins, list):
        return []
    out = []
    for w in wins:
        if not isinstance(w, dict) or not w.get("device") or not w.get("start") or not w.get("end"):
            print(f"[warn] 跳过字段不全的窗口：{w}", file=sys.stderr)
            continue
        try:
            out.append({
                "id": w.get("id") or "",
                "device": str(w["device"]),
                "operator": w.get("operator") or "",
                "reason": w.get("reason") or "",
                "start": parse_dt(w["start"]),
                "end": parse_dt(w["end"]),
                "note": w.get("note") or "",
                "created_at": w.get("created_at") or "",
            })
        except Exception as e:
            print(f"[warn] 窗口时间无法解析，跳过 {w}：{e}", file=sys.stderr)
    out.sort(key=lambda w: w["start"])
    return out


def save_windows(path, windows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    doc = {"windows": [{
        "id": w["id"], "device": w["device"], "operator": w["operator"],
        "reason": w["reason"], "note": w["note"], "created_at": w["created_at"],
        "start": w["start"].isoformat(), "end": w["end"].isoformat(),
    } for w in windows]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)          # 原子替换，避免 cron 读到半个文件


def overlap_window(device, ev_start, ev_end, windows):
    """返回与该事件区间相交的维护窗口；多个时取 start 最近的。

    用相交而不是包含：窗口起点常常是"估计值"（断电时刻未知），
    要求包含会漏判掉"事件起点略早于窗口起点"的情况。
    """
    hit = None
    for w in windows:
        if w["device"] != device:
            continue
        if w["start"] <= ev_end and w["end"] >= ev_start:
            if hit is None or w["start"] > hit["start"]:
                hit = w
    return hit


def classify(events, windows, device, now):
    """给每个断流事件贴上三态标签。不改原事件字段，只新增。"""
    out = []
    for e in events:
        start = parse_dt(e["started_at"])
        end = parse_dt(e["ended_at"]) if e.get("ended_at") else now
        w = overlap_window(device, start, end, windows)
        if w is None:
            state = STATE_OUTAGE
        elif end <= w["end"]:
            state = STATE_MAINTENANCE     # 窗口内恢复 / 仍在窗口内静默
        else:
            state = STATE_OVERRUN          # 超过预定恢复时间
        rec = dict(e)
        rec["state"] = state
        rec["should_notify"] = state in NOTIFY_STATES
        rec["counts_as_outage"] = state in COUNT_AS_OUTAGE
        rec["window"] = None if w is None else {
            "id": w["id"], "operator": w["operator"], "reason": w["reason"],
            "note": w["note"],
            "start": w["start"].isoformat(), "end": w["end"].isoformat(),
            "expired": now > w["end"],
        }
        if state == STATE_OVERRUN:
            rec["overdue_s"] = round((end - w["end"]).total_seconds(), 1)
        out.append(rec)
    return out


def summarize(device, times, events, windows, period_s, now, anchor=None):
    """顶层状态 + 页面要的两个布尔量。

    **计划停机时 `device_offline` / `data_stale` 仍然是 True** ——
    用户明确要求：计划停机也要显示"设备离线、数据已过期"，只是不告警。

    `anchor` = 该设备全表末条（回看窗口内可能一条都没有）。
    窗口内没样本时用它判"离线/过期"；全表都没有则仍是 `None`，不编造。
    """
    last = times[-1] if times else anchor
    silent = round((now - last).total_seconds(), 1) if last else None
    thr = 2 * period_s
    stale = True if last is None else silent > thr

    # 顶层状态看"当前还没结束的那次静默"；只有已结束事件说明现在已恢复
    ongoing = [e for e in events if e["kind"] == "ongoing"]
    if ongoing:
        state = ongoing[-1]["state"]
    else:
        state = STATE_OK

    active = None
    for w in windows:
        if w["device"] == device and w["start"] <= now <= w["end"]:
            active = w
    return {
        "device": device,
        "state": state,
        "should_notify": state in NOTIFY_STATES,
        "device_offline": stale,
        "data_stale": stale,
        "last_seen": last.isoformat() if last else None,
        "silent_seconds": silent,
        "threshold_s": round(thr, 1),
        "in_maintenance_window": active is not None,
        "maintenance": None if active is None else {
            "id": active["id"], "operator": active["operator"],
            "reason": active["reason"], "note": active["note"],
            "start": active["start"].isoformat(), "end": active["end"].isoformat(),
        },
        "outage_count": sum(1 for e in events if e["counts_as_outage"]),
    }


# ────────────────────────────── 断流检测（原逻辑不变）──────────────────────
def find_outages(times, period_s: float, n: int, now=None, anchor=None):
    """纯函数：给一串升序的入库时刻，返回断流事件列表。

    返回每项：{
        kind: 'ended' | 'ongoing',
        started_at, ended_at(ended 才有), gap_count, max_gap_s, threshold_s
    }

    `anchor`：回看窗口内 0 条样本时的"最后一次遥测时刻"（全表末条）。
    给空 `times` 配上它才能判出进行中的断流；不给则什么都不判（不编造）。
    """
    if not times and anchor is None:
        return []
    thr = 2 * period_s
    out = []
    run = []          # 连续超阈值的间隔：[(prev_ts, cur_ts, gap_s), ...]

    def flush():
        if len(run) >= n:
            started = run[0][0]
            ended = run[-1][1]
            out.append({
                "kind": "ended",
                "started_at": started.isoformat(),
                "ended_at": ended.isoformat(),
                "gap_count": len(run),
                "max_gap_s": round(max(r[2] for r in run), 1),
                "threshold_s": round(thr, 1),
            })
        run.clear()

    for prev, cur in zip(times, times[1:]):
        gap = (cur - prev).total_seconds()
        if gap > thr:
            run.append((prev, cur, gap))
        else:
            flush()
    flush()

    last = times[-1] if times else anchor
    if now is not None and (now - last).total_seconds() > thr:
        out.append({
            "kind": "ongoing",
            "started_at": last.isoformat(),
            "ended_at": None,
            "silent_s": round((now - last).total_seconds(), 1),
            "threshold_s": round(thr, 1),
        })
    return out


# ────────────────────────────── 落盘 ──────────────────────────────
def load_events(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def append_events(path, events, device):
    """去重键 = (device, started_at, state)。

    为什么把 state 放进键里：同一次静默可能先被记为 `maintenance`，
    窗口到期后变成 `maintenance_overrun`。这是**状态演进了**，不是重复记，
    两条都保留，事后才能看出"计划停机超期了多久"。同一状态不会重复写。
    """
    existing = {(e.get("device"), e.get("started_at"), e.get("state"))
                for e in load_events(path)}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    added = 0
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            key = (device, e["started_at"], e.get("state"))
            if key in existing:
                continue
            rec = {"device": device, "detected_at":
                   datetime.now(timezone.utc).isoformat()}
            rec.update(e)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            existing.add(key)
            added += 1
    return added


# ────────────────────────────── CLI ──────────────────────────────
STATE_CN = {
    STATE_OK: "正常运行",
    STATE_MAINTENANCE: "计划停机",
    STATE_OUTAGE: "意外断流",
    STATE_OVERRUN: "计划停机超期未恢复",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_SYNC_URL", ""))
    ap.add_argument("--device", default="JY001")
    ap.add_argument("--period", type=float, default=60.0, help="记录周期（秒）")
    ap.add_argument("--n", type=int, default=3, help="连续多少条超阈值才算断流")
    ap.add_argument("--lookback-hours", type=int, default=48)
    ap.add_argument("--events-file", default=DEFAULT_EVENTS)
    ap.add_argument("--maintenance-file", default=DEFAULT_MAINTENANCE)
    ap.add_argument("--no-write", action="store_true", help="只打印，不落盘")
    ap.add_argument("--json", action="store_true")

    ap.add_argument("--add-maintenance", metavar="DEVICE",
                    help="登记一个计划停机窗口后退出")
    ap.add_argument("--operator", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    # 登记窗口：不查库、不落事件，写完即退
    if a.add_maintenance:
        if not (a.start and a.end):
            print("--add-maintenance 必须同时给 --start 和 --end", file=sys.stderr)
            return 2
        wins = load_windows(a.maintenance_file)
        w = {
            "id": f"mw-{datetime.now().strftime('%Y%m%d%H%M%S')}-{a.add_maintenance}",
            "device": a.add_maintenance, "operator": a.operator, "reason": a.reason,
            "note": a.note, "start": parse_dt(a.start), "end": parse_dt(a.end),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if w["end"] <= w["start"]:
            print("--end 必须晚于 --start", file=sys.stderr)
            return 2
        wins.append(w)
        wins.sort(key=lambda x: x["start"])
        save_windows(a.maintenance_file, wins)
        print(f"已登记计划停机：{w['device']} {w['start'].isoformat()} → {w['end'].isoformat()}")
        print(f"  操作人={w['operator'] or '（未填）'}  原因={w['reason'] or '（未填）'}  id={w['id']}")
        print(f"  窗口数={len(wins)} → {a.maintenance_file}")
        return 0

    if not a.db_url:
        print("缺少 DSN：用 --db-url 或设 DATABASE_SYNC_URL", file=sys.stderr)
        return 2

    conn = psycopg2.connect(a.db_url)
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL % {"hours": int(a.lookback_hours)}, {"dev": a.device})
            times = [r[0] for r in cur.fetchall()]
            # 回看窗口内 0 条时，取全表末条当锚点（见文件头「最后一条必须取全表」）
            cur.execute(SQL_LAST, {"dev": a.device})
            row = cur.fetchone()
            anchor = row[0] if row else None
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    windows = load_windows(a.maintenance_file)
    raw = find_outages(times, a.period, a.n, now=now, anchor=anchor)
    events = classify(raw, windows, a.device, now)
    summary = summarize(a.device, times, events, windows, a.period, now,
                        anchor=anchor)
    added = 0 if a.no_write else append_events(a.events_file, events, a.device)

    if a.json:
        doc = dict(summary)
        doc.update({"rows": len(times), "rows_in_window": len(times),
                    "anchor": anchor.isoformat() if anchor else None,
                    "events": events, "added": added})
        print(json.dumps(doc, ensure_ascii=False))
        return 0

    print(f"设备={a.device}  窗口={a.lookback_hours}h  样本={len(times)} 条  "
          f"周期={a.period}s  阈值={2 * a.period}s  连续阈值={a.n} 条")
    print(f"最后一条：{times[-1] if times else (anchor.isoformat() if anchor else '（无数据）')}"
          f"{'  ← 来自全表末条（已超出回看窗口）' if not times and anchor else ''}")
    print(f"当前状态：{STATE_CN.get(summary['state'], summary['state'])}"
          f"   设备离线={summary['device_offline']}  数据过期={summary['data_stale']}"
          f"  需通知={summary['should_notify']}")
    if summary["maintenance"]:
        m = summary["maintenance"]
        print(f"  计划停机：{m['operator'] or '操作人未填'} / {m['reason'] or '原因未填'}  "
              f"{m['start']} → {m['end']}")
    if not events:
        print("断流事件：无")
    for e in events:
        tag = STATE_CN.get(e["state"], e["state"])
        if e["kind"] == "ended":
            print(f"  [{tag}] {e['started_at']} → {e['ended_at']}  "
                  f"连续 {e['gap_count']} 条超阈值，最长 {e['max_gap_s']}s")
        else:
            print(f"  [{tag}] 自 {e['started_at']} 起静默 {e['silent_s']}s "
                  f"（阈值 {e['threshold_s']}s）")
        if e.get("state") == STATE_OVERRUN:
            print(f"        超出计划恢复时间 {e['overdue_s']}s，需人工确认")
    print(f"本次新记 {added} 条 → {a.events_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
