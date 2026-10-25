#!/usr/bin/env python3
"""T5：解析错误的**只读统计口径**（在 007/接线之前也能用）。

## 为什么还要这个脚本

`scripts/parse_error_stats.sql` 依赖迁移 007 建的 `parse_error_stats` 表与
`raw_messages.parse_error_kind` 列 —— 这两样**生产都没有**（007 从未执行）。
所以在生产上跑那份 SQL 只能得到两行报错（2026-10-25 夜间实测）：

    ERROR: relation "parse_error_stats" does not exist
    ERROR: column "parse_error_kind" does not exist

而"解析错误到底有多少、是什么"这个问题不能一直悬着。本脚本用**服务日志**回答
在接线之前能回答的那部分：总数、按天、以及**按出现节律分类**。

## 能给出什么、不能给出什么（边界必须写清楚）

日志行形如：

    2026-10-17 11:55:22,307 [WARNING] app.mqtt.mqtt_service: [MQTT-V9] JSON解析失败 topic=agri/device/JY001/up

**不含报文内容** ⇒ 无法逐条判定"非 JSON / 非本设备 / 重复注册包 / 字段缺失"。
能判的是**相邻两条失败之间的时间间隔**：

  * `periodic`  间隔落在 cadence±tol（默认 30±2s）→ 等周期上行源；
  * `burst`     间隔 <= 5s → 突发重发；
  * `sporadic`  间隔 > 300s → 零星（中间长期没有失败）；
  * `other`     其余。

因此 `periodic` 的占比是**推定**（与已知的"每 30s 一条注册包"节律吻合），
**不是逐条证实**。要逐条证实必须先落原始报文（T5 接线 + 007），那是需要人批准的改动。

## 用法

    python3 scripts/parse_error_cadence.py                      # 读 journalctl -u greenhouse
    python3 scripts/parse_error_cadence.py --log-file /tmp/pe.txt
    python3 scripts/parse_error_cadence.py --json

只读：只跑 journalctl / 读文件，不写库、不改服务。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
DEFAULT_PATTERN = "JSON解析失败"
DEFAULT_UNIT = "greenhouse"

BURST_MAX = 5.0          # 间隔 <= 5s 视为突发重发
SPORADIC_MIN = 300.0     # 间隔 > 300s 视为零星


def parse_lines(text: str, pattern: str = DEFAULT_PATTERN):
    """从日志文本里取出匹配行的时刻（升序）。"""
    out = []
    for line in text.splitlines():
        if pattern not in line:
            continue
        m = TS_RE.match(line.strip())
        if m:
            out.append(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    out.sort()
    return out


def intervals(ts):
    return [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]


def classify_intervals(gaps, cadence=30.0, tol=2.0):
    """把间隔分成四类（计数 + 占比）。间隔数为 len(gaps)。"""
    c = Counter()
    for g in gaps:
        if g <= BURST_MAX:
            c["burst"] += 1
        elif g > SPORADIC_MIN:
            c["sporadic"] += 1
        elif abs(g - cadence) <= tol:
            c["periodic"] += 1
        else:
            c["other"] += 1
    total = len(gaps)
    return {k: {"count": v, "pct": round(100.0 * v / total, 1) if total else 0.0}
            for k, v in c.items()}


def summarize(ts, cadence=30.0, tol=2.0, pattern=DEFAULT_PATTERN):
    gaps = intervals(ts)
    sorted_gaps = sorted(gaps)
    mid = sorted_gaps[len(sorted_gaps) // 2] if sorted_gaps else None
    return {
        "pattern": pattern,
        "total": len(ts),
        "first_at": ts[0].strftime("%Y-%m-%d %H:%M:%S") if ts else None,
        "last_at": ts[-1].strftime("%Y-%m-%d %H:%M:%S") if ts else None,
        "by_day": dict(sorted(Counter(t.strftime("%Y-%m-%d") for t in ts).items())),
        "intervals": len(gaps),
        "median_interval_s": mid,
        "cadence_s": cadence,
        "tolerance_s": tol,
        "classes": classify_intervals(gaps, cadence, tol),
        "caveat": ("日志不落报文内容，periodic 是按出现节律的推定，"
                   "不是逐条分类；逐条分类需先落原始报文（T5 接线 + 007）"),
    }


def read_journal(unit: str) -> str:
    try:
        return subprocess.run(
            ["journalctl", "-u", unit, "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=120).stdout
    except FileNotFoundError:
        print("journalctl 不可用，请改用 --log-file", file=sys.stderr)
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-file", help="直接读日志文件（不给就读 journalctl）")
    ap.add_argument("--unit", default=DEFAULT_UNIT)
    ap.add_argument("--pattern", default=DEFAULT_PATTERN)
    ap.add_argument("--cadence", type=float, default=30.0)
    ap.add_argument("--tol", type=float, default=2.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    text = ""
    if a.log_file:
        with open(a.log_file, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = read_journal(a.unit)

    ts = parse_lines(text, a.pattern)
    doc = summarize(ts, a.cadence, a.tol, a.pattern)

    if a.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0

    print("=" * 72)
    print(f"解析错误只读统计（口径：日志匹配 {a.pattern!r}）")
    print("=" * 72)
    if not doc["total"]:
        print("没有匹配到任何条目。")
        return 0
    print(f"总条数   : {doc['total']}")
    print(f"首条/末条: {doc['first_at']}  →  {doc['last_at']}")
    print(f"间隔中位 : {doc['median_interval_s']}s（对照周期 {a.cadence}s ±{a.tol}s）")
    print("\n按天：")
    for d, n in doc["by_day"].items():
        print(f"  {d}  {n}")
    print("\n按出现节律分类（共 %d 段间隔）：" % doc["intervals"])
    names = {"periodic": "等周期（推定：30s 注册包一类）",
             "burst": f"突发重发（间隔<={BURST_MAX:.0f}s）",
             "sporadic": f"零星（间隔>{SPORADIC_MIN:.0f}s）",
             "other": "其它"}
    for k in ("periodic", "burst", "sporadic", "other"):
        v = doc["classes"].get(k)
        if v:
            print(f"  {names[k]:<28} {v['count']:>6}  {v['pct']:>5}%")
    print(f"\n⚠ {doc['caveat']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
