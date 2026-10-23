#!/usr/bin/env python3
"""断流观测：连续 N 条间隔 > 2×记录周期 → 记一次**断流事件**。

**只记录，不告警到人**。落盘是一行一个 JSON 的本地文件（默认
`/opt/greenhouse-nightly/logs/outage-events.jsonl`），不写业务库、不发消息、
不动设备。用途是让"设备什么时候开始不传了"变成可查的记录，而不是靠人发现。

为什么是 2×周期 + 连续 N 条：
  * 单条间隔超阈值太敏感 —— 网络抖动、补传、服务端重启都会造成一次长间隔；
  * 连续 N 条都超，才说明"这个设备真的停了"，而不是"有一条迟到"。

判据（都按 `created_at` = 入库时刻算，设备侧没有可信采集时间）：
  1. 已结束的断流：连续 >= N 条相邻间隔 > 2×period
  2. 进行中的断流：`now - 最后一条` > 2×period（不必等够 N 条）
     —— 这一条正是 2026-10-23 08:55 那次的情况：设备上行停了，
        而 EMQX 因为 keepalive=0 一直显示 connected（见 docs/outage-triage-20261023.md）。

用法：
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

# device_id 可能是 devices.id（uuid）也可能是 devices.device_id（'JY001'），两种都认
SQL = """
SELECT t.created_at
  FROM telemetry t
  JOIN devices d ON d.id = t.device_id
 WHERE (d.device_id = %(dev)s OR d.id = %(dev)s)
   AND t.created_at >= now() - interval '%(hours)s hours'
 ORDER BY t.created_at
""".replace("%(dev)s", "%%(dev)s")   # 保留 psycopg2 的命名参数，只让 hours 走 % 格式化


def find_outages(times, period_s: float, n: int, now=None):
    """纯函数：给一串升序的入库时刻，返回断流事件列表。

    返回每项：{
        kind: 'ended' | 'ongoing',
        started_at, ended_at(ended 才有), gap_count, max_gap_s, threshold_s
    }
    """
    if not times:
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

    last = times[-1]
    if now is not None and (now - last).total_seconds() > thr:
        out.append({
            "kind": "ongoing",
            "started_at": last.isoformat(),
            "ended_at": None,
            "silent_s": round((now - last).total_seconds(), 1),
            "threshold_s": round(thr, 1),
        })
    return out


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
    """同一 (device, kind, started_at) 不重复记。返回本次新增条数。"""
    existing = {(e.get("device"), e.get("kind"), e.get("started_at"))
                for e in load_events(path)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    added = 0
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            key = (device, e["kind"], e["started_at"])
            if key in existing:
                continue
            rec = {"device": device, "detected_at":
                   datetime.now(timezone.utc).isoformat()}
            rec.update(e)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            existing.add(key)
            added += 1
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_SYNC_URL", ""))
    ap.add_argument("--device", default="JY001")
    ap.add_argument("--period", type=float, default=60.0, help="记录周期（秒）")
    ap.add_argument("--n", type=int, default=3, help="连续多少条超阈值才算断流")
    ap.add_argument("--lookback-hours", type=int, default=48)
    ap.add_argument("--events-file", default=DEFAULT_EVENTS)
    ap.add_argument("--no-write", action="store_true", help="只打印，不落盘")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not a.db_url:
        print("缺少 DSN：用 --db-url 或设 DATABASE_SYNC_URL", file=sys.stderr)
        return 2

    conn = psycopg2.connect(a.db_url)
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL % {"hours": int(a.lookback_hours)}, {"dev": a.device})
            times = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    events = find_outages(times, a.period, a.n, now=now)
    added = 0 if a.no_write else append_events(a.events_file, events, a.device)

    if a.json:
        print(json.dumps({"device": a.device, "rows": len(times),
                          "last_seen": times[-1].isoformat() if times else None,
                          "events": events, "added": added},
                         ensure_ascii=False))
    else:
        print(f"设备={a.device}  窗口={a.lookback_hours}h  样本={len(times)} 条  "
              f"周期={a.period}s  阈值={2 * a.period}s  连续阈值={a.n} 条")
        print(f"最后一条：{times[-1] if times else '（无数据）'}")
        if not events:
            print("断流事件：无")
        for e in events:
            if e["kind"] == "ended":
                print(f"  [已结束] {e['started_at']} → {e['ended_at']}  "
                      f"连续 {e['gap_count']} 条超阈值，最长 {e['max_gap_s']}s")
            else:
                print(f"  [进行中] 自 {e['started_at']} 起静默 {e['silent_s']}s "
                      f"（阈值 {e['threshold_s']}s）")
        print(f"本次新记 {added} 条 → {a.events_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
