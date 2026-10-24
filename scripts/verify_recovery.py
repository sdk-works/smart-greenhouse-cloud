#!/usr/bin/env python3
"""设备重新上电后的恢复验收（只读）。

## 为什么单独做一个脚本

断流之后最容易犯的错不是"没看出恢复"，而是**把空白算成了丢包**：

  "断流 26 小时，本应 1560 条，实际 0 条，丢包率 100%"   ← 错的

设备断电期间**没有采样**，这 1560 条从来没有存在过。把它计入分母，
会永久污染到报率/完整率，而且事后分不清哪些是"真丢"、哪些是"根本没采"。
本脚本的口径是：**断电窗口整体排除，不推算应达条数，丢包固定记 0**，
并在输出里显式写出这条规则，让人看得见口径而不是猜。

## 验的四件事

1. **新 boot_id**：断电前 boot 31，恢复后必须是新 boot。仍是 31 → MCU 没重启
   （可能只是通信恢复、或压根没断电），要复查。
2. **seq 重新从 1 开始**：与 boot 变化互相印证。
3. **恢复后节律**：相邻间隔中位数应接近记录周期（默认 60s），
   并给出落在容差内的比例——节律乱了说明设备在补传或时钟异常。
4. **断电窗口内零数据**：窗口里如果冒出遥测，说明是缓存补传或有人补数，
   必须逐条列出来，不能默默忽略。

## 用法

  python3 scripts/verify_recovery.py --db-url "$DSN" --device JY001 \\
      --outage-start "2026-10-23T08:54:18+08:00" --period 60

  # 也可以直接从维护窗口文件读断流起止
  python3 scripts/verify_recovery.py --db-url "$DSN" --device JY001 \\
      --maintenance-file /opt/greenhouse-nightly/logs/maintenance.json

设备还没上电时，脚本输出"未恢复"并 **exit 0** —— 那是状态，不是脚本错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone

import psycopg2

MSG_RE = re.compile(r"^(?P<dev>.+)/(?P<boot>\d+)/(?P<seq>\d+)$")

STATE_RECOVERED = "recovered"
STATE_NOT_YET = "not_recovered"
STATE_PARTIAL = "recovered_with_issues"


def parse_dt(s):
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    txt = str(s).strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    dt = datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def parse_msg(msg_id):
    """`JY001/31/922` → (boot=31, seq=922)；格式不符返回 (None, None)。"""
    if not msg_id:
        return None, None
    m = MSG_RE.match(str(msg_id).strip())
    if not m:
        return None, None
    return int(m.group("boot")), int(m.group("seq"))


def split_at_last_gap(rows, period_s=60.0, mult=2.0):
    """在序列里找**最后一个**长间隔，从那里把 断电前 / 恢复后 切开。

    为什么不能直接用人工填的 `--outage-start`：
      断电时刻未知时那个值是"最后一条遥测时刻"，于是 `created_at >= start`
      会把断流前那条**正常数据**也捞进来，当成"恢复后的第一条"，
      结论就变成"已恢复、boot 没变、seq 没归 1"——完全反了。

    按数据自身的长间隔切，不依赖任何人工估计：
      * 设备还没上电 → 序列内部没有长间隔（断流发生在末尾之后）→ 返回 None；
      * 设备已恢复 → 末尾之前必有一段 > 2×period 的间隔，那就是断点。

    返回断点左端下标 i（rows[i+1:] 才是恢复后的数据）；无断点返回 None。
    """
    thr = 2 * period_s
    for i in range(len(rows) - 2, -1, -1):
        if (rows[i + 1][0] - rows[i][0]).total_seconds() > thr:
            return i
    return None


def analyze(pre, post, period_s=60.0, tolerance=0.25, now=None, currently_silent=None):
    """pre/post 都是 [(created_at, msg_id)]，按时间升序。

    `currently_silent=True` 表示"序列末尾之后设备就没再传了"。
    这个参数必须单独传：**"恢复过"和"现在在线"是两件事**。
    只看最后一个长间隔会找到上一次重启，把 29 小时前的旧恢复判成"已恢复"。

    返回验收结果字典。**不改库、不推算、不补数**。
    """
    out = {
        "state": STATE_NOT_YET,
        "checks": [],
        "period_s": period_s,
    }

    def add(name, ok, detail, level="error"):
        out["checks"].append({"name": name, "ok": ok, "detail": detail,
                              "level": "ok" if ok else level})

    # 断电前基线
    pre_boot = pre_seq = pre_at = None
    for at, mid in reversed(pre):
        b, s = parse_msg(mid)
        if b is not None:
            pre_boot, pre_seq, pre_at = b, s, at
            break
    out["before"] = {
        "boot": pre_boot, "seq": pre_seq,
        "last_at": pre_at.isoformat() if pre_at else None,
    }

    if not post:
        add("设备已恢复", False,
            "数据序列里找不到断点（长间隔都出现在末尾之后）—— 断流仍在持续，设备尚未上电")
        out["after"] = None
        out["accounting"] = _accounting(None, None, period_s)
        return out

    first_at, first_mid = post[0]
    post_boot, post_seq = parse_msg(first_mid)
    last_at, last_mid = post[-1]
    out["after"] = {
        "boot": post_boot, "seq": post_seq,
        "first_at": first_at.isoformat(),
        "last_at": last_at.isoformat(),
        "last_msg_id": last_mid,
        "rows": len(post),
    }

    # 1. boot 变了没有
    if post_boot is None:
        add("新 boot_id", False, f"首条 msg_id 无法解析：{first_mid}", "warning")
    elif pre_boot is None:
        add("新 boot_id", True, f"断电前无基线，恢复后 boot={post_boot}（无法比对）", "warning")
    else:
        changed = post_boot != pre_boot
        add("新 boot_id", changed,
            f"boot {pre_boot} → {post_boot}" if changed
            else f"boot 仍为 {pre_boot} —— MCU 没有重启过，需确认是否真的断过电")

    # 2. seq 是否重新从 1 开始
    if post_seq is not None:
        add("seq 重新开始", post_seq == 1,
            f"恢复首条 seq={post_seq}" + ("（正常重启）" if post_seq == 1
                                          else f"，而不是 1 —— 疑似缓存续传或未重启"))

    # 3. 恢复后 seq 连续性
    boots = {b for _, m in post for b, s in [parse_msg(m)] if b is not None}
    if len(boots) > 1:
        add("恢复后 boot 一致", False, f"恢复后出现多个 boot：{sorted(boots)}", "warning")
    gaps = []
    dups = []
    seen = set()
    prev = None
    for b, s in (parse_msg(m) for _, m in post):
        if s is None:
            continue
        if s in seen:
            dups.append(s)
        seen.add(s)
        if prev is not None and s != prev + 1:
            gaps.append((prev, s))
        prev = s
    if gaps or dups:
        detail = f"跳号 {len(gaps)} 处，重复 {len(dups)} 条"
        if gaps[:3]:
            detail += "；例：" + "、".join(f"{a}→{b}" for a, b in gaps[:3])
        add("恢复后 seq 连续", False, detail, "warning")
    else:
        add("恢复后 seq 连续", True, f"{len(post)} 条无跳号、无重复")

    # 4. 上传节律
    times = [at for at, _ in post]
    intervals = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    lo, hi = period_s * (1 - tolerance), period_s * (1 + tolerance)
    in_range = [x for x in intervals if lo <= x <= hi]
    if intervals:
        med = round(statistics.median(intervals), 1)
        ratio = round(len(in_range) / len(intervals) * 100, 1)
        # 首条之后的第一段间隔天然包含"停机时长"，不算节律样本
        ok = med <= hi and ratio >= 60
        add("上传节律", ok,
            f"样本 {len(intervals)} 段，中位 {med}s（期望 {period_s}s），"
            f"落在 {lo:.0f}~{hi:.0f}s 的占 {ratio}%")
        out["cadence"] = {"median_s": med, "in_range_pct": ratio,
                          "samples": len(intervals),
                          "min_s": round(min(intervals), 1),
                          "max_s": round(max(intervals), 1)}
    else:
        add("上传节律", False, "恢复后只有 1 条，间隔无法计算", "warning")
        out["cadence"] = None

    # 5. 断电窗口内不该有任何数据（有就是补传/伪造）
    if currently_silent:
        out["accounting"] = _accounting(None, None, period_s,
                                        ongoing_from=post[-1][0], now=now)
    else:
        out["accounting"] = _accounting(pre_at, first_at, period_s)

    failed = [c for c in out["checks"] if not c["ok"]]

    # 6. 当前是否还在传（与"历史上恢复过"分开判）
    if post:
        tail_gap = (now - post[-1][0]).total_seconds() if now else 0.0
        silent_now = tail_gap if currently_silent is None else bool(currently_silent)
        out["silent_now_s"] = round(tail_gap, 1)
        if silent_now:
            add("当前在线", False,
                f"最近一次恢复后收到 {len(post)} 条，但最新一条停在 {last_at.isoformat()}，"
                f"至今静默 {round(tail_gap)}s（阈值 {round(2 * period_s)}s）—— 当前不在线",
                "error")
        else:
            add("当前在线", True, f"最新一条 {last_at.isoformat()}，静默 {round(tail_gap)}s")

    failed = [c for c in out["checks"] if not c["ok"]]
    out["state"] = STATE_PARTIAL if failed else STATE_RECOVERED
    out["currently_silent"] = bool(currently_silent) if currently_silent is not None else False
    return out


def _accounting(pre_at, first_at, period_s, ongoing_from=None, now=None):
    """空白口径：断电期间无采样，应达 0 条、丢包 0 条。

    `ongoing_from` 表示断流还没结束——此时真正的空白是"最后一条 → 现在"，
    而不是历史上某次重启的间隔。用错区间会把 3 分钟的旧重启间隔
    当成 29 小时断电的空白，口径就毁了。
    """
    gap_s = None
    ongoing = ongoing_from is not None and now is not None
    if ongoing:
        gap_s = round((now - ongoing_from).total_seconds(), 1)
    elif pre_at and first_at:
        gap_s = round((first_at - pre_at).total_seconds(), 1)
    return {
        "rule": "outage_window_excluded",
        "note": "设备断电期间没有采样，这段空白不计入丢包、不推算应达条数、不补数据",
        "gap_seconds": gap_s,
        "gap_ongoing": ongoing,
        "expected_samples_during_gap": 0,
        "dropped_samples": 0,
        "counted_in_completeness": False,
    }


def find_outage_window(windows, device):
    """从维护窗口里取该设备的断电起点/终点。取最后一个（最近的）窗口。"""
    hit = None
    for w in windows:
        if w.get("device") == device:
            hit = w
    if not hit:
        return None, None
    return parse_dt(hit["start"]), parse_dt(hit["end"])


def load_windows(path):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        print(f"[warn] 维护窗口文件无法解析：{e}", file=sys.stderr)
        return []
    wins = doc.get("windows") if isinstance(doc, dict) else doc
    return wins if isinstance(wins, list) else []


SQL = """
SELECT t.created_at, t.msg_id
  FROM telemetry t
  JOIN devices d ON d.id = t.device_id
 WHERE (d.device_id = %(dev)s OR d.id = %(dev)s)
   AND t.created_at >= now() - (%(days)s * interval '1 day')
 ORDER BY t.created_at DESC
 LIMIT 5000
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_SYNC_URL", ""))
    ap.add_argument("--device", default="JY001")
    ap.add_argument("--period", type=float, default=60.0)
    ap.add_argument("--tolerance", type=float, default=0.25, help="节律容差（比例）")
    ap.add_argument("--outage-start", default="", help="断电起点（仅展示用，判定不依赖它）")
    ap.add_argument("--lookback-days", type=int, default=7, help="扫描最近多少天的数据")
    ap.add_argument("--maintenance-file", default="/opt/greenhouse-nightly/logs/maintenance.json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not a.db_url:
        print("缺少 DSN：用 --db-url 或设 DATABASE_SYNC_URL", file=sys.stderr)
        return 2

    # 断电起点只用于展示与对照，**判定不依赖它**（它通常是估计值）
    start = parse_dt(a.outage_start) if a.outage_start else None
    if start is None:
        start, _ = find_outage_window(load_windows(a.maintenance_file), a.device)

    conn = psycopg2.connect(a.db_url)
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL, {"dev": a.device, "days": int(a.lookback_days)})
            rows_all = [(r[0], r[1]) for r in reversed(cur.fetchall())]
    finally:
        conn.close()

    if not rows_all:
        print(f"最近 {a.lookback_days} 天没有该设备的任何数据，无法验收", file=sys.stderr)
        return 2

    # 按数据自身的长间隔切：断流还没结束时没有断点 → post 为空 → 判"未恢复"
    now = datetime.now(timezone.utc)
    silent_s = (now - rows_all[-1][0]).total_seconds()
    currently_silent = silent_s > 2 * a.period
    idx = split_at_last_gap(rows_all, a.period)
    if idx is None:
        pre, post = rows_all[-200:], []
    else:
        pre, post = rows_all[max(0, idx - 199):idx + 1], rows_all[idx + 1:]

    res = analyze(pre, post, a.period, a.tolerance, now=now,
                  currently_silent=currently_silent)
    res["silent_now_s"] = round(silent_s, 1)
    res["device"] = a.device
    res["rows_scanned"] = len(rows_all)
    res["outage_start"] = (start or (pre[-1][0] if pre else None)).isoformat() \
        if (start or pre) else None
    res["split_source"] = "data_gap" if idx is not None else "no_gap_yet"

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    b, af = res.get("before") or {}, res.get("after") or {}
    print(f"恢复验收 · 设备={a.device}  断电起点={res.get('outage_start') or '未知'}"
          f"  周期={a.period}s  扫描 {res['rows_scanned']} 条")
    if b.get("boot") is not None:
        print(f"断电前：boot {b['boot']} seq {b['seq']}  最后一条 {b['last_at']}")
    else:
        print("断电前：无可用基线")
    if af:
        print(f"恢复后：boot {af.get('boot')} seq {af.get('seq')}  首条 {af.get('first_at')}"
              f"  共 {af['rows']} 条")
    if res["state"] == STATE_NOT_YET:
        text = "尚未恢复（设备未上电，序列里没有断点）"
    elif res.get("currently_silent"):
        text = "当前未在线（曾恢复过，但现在没有数据）"
    elif res["state"] == STATE_PARTIAL:
        text = "已恢复，但有需要确认的问题"
    else:
        text = "已恢复，全部检查通过"
    print(f"结论：{text}")
    print()
    for c in res["checks"]:
        mark = "✓" if c["ok"] else ("!" if c["level"] == "warning" else "✗")
        print(f"  [{mark}] {c['name']}：{c['detail']}")
    acc = res["accounting"]
    print()
    print(f"空白口径：{acc['note']}")
    if acc["gap_seconds"] is not None:
        tag = "（断流仍在进行）" if acc.get("gap_ongoing") else ""
        print(f"  空白时长 {acc['gap_seconds']}s（{acc['gap_seconds'] / 3600:.1f}h）{tag} → "
              f"应达 {acc['expected_samples_during_gap']} 条，丢包 {acc['dropped_samples']} 条，"
              f"计入完整率={acc['counted_in_completeness']}")
    elif res.get("silent_now_s") is not None:
        print(f"  至今静默 {res['silent_now_s']}s → 应达 0 条，丢包 0 条，"
              f"计入完整率=False")
    return 0


if __name__ == "__main__":
    sys.exit(main())
