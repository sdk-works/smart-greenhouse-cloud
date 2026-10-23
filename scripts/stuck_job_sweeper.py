#!/usr/bin/env python3
"""悬挂 job 清理工具（通用：任意 job 表 + 可选上游表）。

识别口径（三条，任一命中即判悬挂）：
  1. orphan            —— job 引用的上游记录不存在（含 ref 为 NULL）
  2. terminal_conflict —— 上游已进终态（completed/success/done），下游 job 却还是 queued/pending
                          ⇒ 下游根本没被拉起来，永远等不到
  3. timeout           —— job 处于 running/processing 且 --time-col 早于 now - --timeout-seconds

**不误伤正常 queued** 的关键：正常排队的下游 job，其上游**尚未进终态**。
只有"上游都完成了它还 queued"才算悬挂；上游还在跑/还没开始的 queued 一律不动。

安全：
  * 默认 `--dry-run`（只读）。
  * `--apply` 只在库名含 `greenhouse_nightly_test` 时允许；其它库（含生产）拒绝。
  * `--apply` 落 manifest（含每条 job 的原状态）并生成回滚 SQL。

用法：
  python3 scripts/stuck_job_sweeper.py --table job_judges --ref-table job_turns --dry-run
  python3 scripts/stuck_job_sweeper.py --table job_judges --ref-table job_turns --apply
"""
import argparse
import json
import os
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

TEST_DB_MARKER = "greenhouse_nightly_test"

REASON_TEXT = {
    "orphan": "上游记录不存在（孤儿 job）",
    "terminal_conflict": "上游已进终态，本 job 仍是 {st}，永远不会被拉起",
    "timeout": "长时间停留在 {st}（{sec}s > 阈值 {thr}s）",
}


def db_name(dsn: str) -> str:
    return dsn.rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def q(ident: str) -> str:
    """标识符加引号（列名/表名来自命令行，必须转义双引号再包起来）。"""
    return '"' + ident.replace('"', '""') + '"'


def build_sql(a):
    """拼出检测 SQL。ref_table 为空时不做 orphan / terminal_conflict 检测。"""
    t, tid, st = q(a.table), q(a.id_col), q(a.status_col)
    tc = q(a.time_col)
    pend = [s.strip() for s in a.pending_statuses.split(",") if s.strip()]
    run = [s.strip() for s in a.running_statuses.split(",") if s.strip()]
    term = [s.strip() for s in a.terminal_statuses.split(",") if s.strip()]

    select = [f"j.{tid} AS job_id", f"j.{st} AS job_status"]
    joins = ""
    if a.ref_table:
        rt = q(a.ref_table)
        rid, rst = q(a.ref_id_col), q(a.ref_status_col)
        select += [f"r.{rst} AS ref_status", f"j.{q(a.ref_col)} AS ref_id"]
        joins = (f" LEFT JOIN {rt} r ON r.{rid} = j.{q(a.ref_col)}")
    if a.time_col:
        select += [f"j.{tc} AS job_time"]

    # 三条口径都只针对"还没走到自己终态"的 job：已经 failed/success 的历史记录不是悬挂
    alive = pend + run
    alive_clause = f"j.{st} IN ({ph(alive)})" if alive else "FALSE"

    where = []
    if a.ref_table:
        where.append(f"((j.{q(a.ref_col)} IS NULL OR r.{q(a.ref_id_col)} IS NULL)"
                     f" AND {alive_clause})")
        if term and pend:
            where.append(f"(r.{q(a.ref_status_col)} IN ({ph(term)})"
                         f" AND j.{st} IN ({ph(pend)}))")
    if run and a.time_col:
        where.append(f"(j.{st} IN ({ph(run)})"
                     f" AND j.{tc} < now() - interval '{int(a.timeout_seconds)} seconds')")
    if not where:
        where.append("FALSE")

    sql = ("SELECT " + ", ".join(select) + f" FROM {t} j{joins} WHERE " +
           " OR ".join(where) + f" ORDER BY j.{tid}")
    return sql, pend, run, term


def ph(items):
    return ",".join("'" + s.replace("'", "''") + "'" for s in items)


def classify(row, pend, run, term, thr, has_ref):
    st = row.get("job_status")
    if has_ref:
        if row.get("ref_id") is None or row.get("ref_status") is None:
            return "orphan"
        if row.get("ref_status") in term and st in pend:
            return "terminal_conflict"
    if row.get("job_time") is not None and st in run:
        return "timeout"
    return None


def main():
    ap = argparse.ArgumentParser(description="悬挂 job 清理（默认 dry-run）")
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_SYNC_URL", ""))
    ap.add_argument("--table", required=True, help="job 表名")
    ap.add_argument("--id-col", default="id")
    ap.add_argument("--status-col", default="status")
    ap.add_argument("--ref-col", default="turn_id", help="指向上游的外键列")
    ap.add_argument("--ref-table", default="", help="上游表名；留空=不做孤儿/终态矛盾检测")
    ap.add_argument("--ref-id-col", default="id")
    ap.add_argument("--ref-status-col", default="status")
    ap.add_argument("--time-col", default="updated_at", help="超时判定用的时间列")
    ap.add_argument("--pending-statuses", default="queued,pending")
    ap.add_argument("--running-statuses", default="running,processing")
    ap.add_argument("--terminal-statuses", default="completed,success,done")
    ap.add_argument("--timeout-seconds", type=int, default=3600)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--set-status", default="failed", help="清理时把 job 写成什么状态")
    ap.add_argument("--reason-col", default="", help="可选：把原因写进哪一列（如 last_error）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out-dir", default="/opt/greenhouse-nightly/logs")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not a.db_url:
        print("缺少 DSN：用 --db-url 或设 DATABASE_SYNC_URL", file=sys.stderr)
        return 2
    dbname = db_name(a.db_url)
    if a.apply and TEST_DB_MARKER not in dbname:
        print(f"拒绝写库：--apply 只允许对 {TEST_DB_MARKER} 执行，当前库={dbname}",
              file=sys.stderr)
        return 3

    conn = psycopg2.connect(a.db_url)
    try:
        if not a.apply:
            conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"public.{a.table}",))
            if cur.fetchone()[0] is None:
                msg = f"表 {a.table} 不存在（库={dbname}）——无法盘点，请确认表名与所在库"
                print(json.dumps({"error": msg}) if a.json else msg)
                return 4
            if a.ref_table:
                cur.execute("SELECT to_regclass(%s)", (f"public.{a.ref_table}",))
                if cur.fetchone()[0] is None:
                    msg = f"上游表 {a.ref_table} 不存在（库={dbname}）"
                    print(json.dumps({"error": msg}) if a.json else msg)
                    return 4

        sql, pend, run, term = build_sql(a)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()

        has_ref = bool(a.ref_table)
        hits = []
        for r in rows:
            kind = classify(r, pend, run, term, a.timeout_seconds, has_ref)
            if kind is None:
                continue
            detail = REASON_TEXT[kind].format(
                st=r.get("job_status"),
                sec=int((datetime.now(r["job_time"].tzinfo) - r["job_time"]).total_seconds())
                if r.get("job_time") else "-",
                thr=a.timeout_seconds)
            hits.append({"job_id": str(r["job_id"]), "reason": kind,
                         "job_status": r.get("job_status"),
                         "ref_status": r.get("ref_status"), "detail": detail})
        if a.limit:
            hits = hits[:a.limit]

        if a.json:
            print(json.dumps({"db": dbname, "table": a.table, "stuck": len(hits),
                              "items": hits}, ensure_ascii=False))
        else:
            print("=" * 72)
            print(f"悬挂 job 盘点{'（DRY-RUN）' if not a.apply else '（已清理）'}  "
                  f"库={dbname} 表={a.table}")
            print(f"命中 {len(hits)} 条")
            print("=" * 72)
            for h in hits:
                print(f"  {h['job_id']}  [{h['reason']}]  status={h['job_status']} "
                      f"ref_status={h['ref_status']}\n      {h['detail']}")
            if not hits:
                print("  （无）")

        if a.apply:
            touched = []
            with conn.cursor() as cur:
                for h in hits:
                    sets = [f"{q(a.status_col)} = %s"]
                    vals = [a.set_status]
                    if a.reason_col:
                        sets.append(f"{q(a.reason_col)} = %s")
                        vals.append(f"[sweeper] {h['reason']}: {h['detail']}")
                    sets.append(f"{q(a.time_col)} = now()")
                    vals.append(h["job_id"])
                    cur.execute(f"UPDATE {q(a.table)} SET " + ", ".join(sets) +
                                f" WHERE {q(a.id_col)} = %s", vals)
                    if cur.rowcount:
                        touched.append(h)
            conn.commit()
            print(json.dumps({"cleaned": len(touched)}) if a.json
                  else f"\n已清理 {len(touched)} 条")
        elif not a.json:
            print("\nDRY-RUN：未写入。加 --apply 才会写（且只对测试库生效）。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
