#!/usr/bin/env python3
"""004 归属回填（attribution）可执行脚本。

把 telemetry 里 `attribution_status IS NULL` 的历史行按**绑定窗口 + 时间证据**分级，
默认 `--dry-run`（只读）。分级口径与 `migrations/004_data_reliability_up.sql` 一致，
并**新增一条护栏**：`sampled_at` 与接收时刻 `timestamp` 偏差 > `--max-skew`（默认 300s）
一律判 `pending/sample_time_implausible`，**不得进可信**。

分级（按判定的先后顺序，命中即返回）：
  1. company_id IS NULL                       -> pending / no_company
  2. coalesce(sampled_at, timestamp) IS NULL   -> pending / legacy_time_missing
  3. |sampled_at - timestamp| > max_skew       -> pending / sample_time_implausible   【护栏】
  4. 覆盖窗口 > 1                              -> pending / conflict_binding_window
  5. 覆盖窗口 = 0                              -> pending / legacy_before_attribution
  6. 恰好 1 个窗口 + 有 sampled_at             -> resolved / backfill_sample_time        （可信）
  7. 恰好 1 个窗口 + 只有接收时间              -> resolved / backfill_receive_time_estimated（推定）

安全边界：
  * `--apply` **只在库名含 `greenhouse_nightly_test` 时允许执行**；其它库（含生产）直接拒绝退出。
  * 生产库只能 `--dry-run`（只读 SELECT），脚本不会发任何 UPDATE/DDL。
  * `--apply` 会把写入的 id 落盘成 manifest，并生成同名的回滚 SQL。
    被处理的行改动前 `attribution_status IS NULL`，因此回滚就是"把这几个 id 恢复成 NULL"。

用法：
  # 生产：只读预演（必须显式给同步 DSN，脚本仍会校验库名不是测试库 → 只允许 dry-run）
  python3 scripts/backfill_attribution.py --db-url "$DATABASE_SYNC_URL" --dry-run --sample 5

  # 测试库：预演 / 限量 / 只写可信 / 实写
  python3 scripts/backfill_attribution.py --dry-run
  python3 scripts/backfill_attribution.py --dry-run --only-trusted --limit 10
  python3 scripts/backfill_attribution.py --apply --limit 100
"""
import argparse
import json
import os
import sys
from collections import OrderedDict
from datetime import datetime

import psycopg2
import psycopg2.extras

TEST_DB_MARKER = "greenhouse_nightly_test"

# 分级 SQL：一次算完，不落任何临时表。%(max_skew)s / %(limit)s 由脚本注入。
CLASSIFY_SQL = """
WITH cand AS (
  SELECT t.id AS tid,
         b.id AS bid,
         b.binding_version AS bv,
         (t.sampled_at IS NOT NULL) AS by_sampled
    FROM telemetry t
    JOIN device_bindings b
      ON b.device_id     = t.device_id
     AND b.greenhouse_id = t.greenhouse_id
     AND b.start_at     <= coalesce(t.sampled_at, t."timestamp")
     AND (b.end_at IS NULL OR b.end_at >= coalesce(t.sampled_at, t."timestamp"))
   WHERE t.attribution_status IS NULL
), agg AS (
  SELECT tid, count(*) AS win_cnt, min(bid) AS bid, min(bv) AS bv,
         bool_and(by_sampled) AS by_sampled
    FROM cand GROUP BY tid
)
SELECT t.id,
       t.msg_id,
       t.company_id,
       t.device_id,
       t.greenhouse_id,
       t."timestamp"            AS receive_time,
       t.sampled_at             AS sample_time,
       t.time_quality,
       CASE
         WHEN t.company_id IS NULL                                    THEN 'pending'
         WHEN coalesce(t.sampled_at, t."timestamp") IS NULL            THEN 'pending'
         WHEN t.sampled_at IS NOT NULL
              AND abs(extract(epoch from (t.sampled_at - t."timestamp"))) > %(max_skew)s
                                                                      THEN 'pending'
         WHEN coalesce(a.win_cnt, 0) > 1                               THEN 'pending'
         WHEN coalesce(a.win_cnt, 0) = 0                               THEN 'pending'
         ELSE 'resolved'
       END AS attribution_status,
       CASE
         WHEN t.company_id IS NULL                                    THEN 'no_company'
         WHEN coalesce(t.sampled_at, t."timestamp") IS NULL            THEN 'legacy_time_missing'
         WHEN t.sampled_at IS NOT NULL
              AND abs(extract(epoch from (t.sampled_at - t."timestamp"))) > %(max_skew)s
                                                                      THEN 'sample_time_implausible'
         WHEN coalesce(a.win_cnt, 0) > 1                               THEN 'conflict_binding_window'
         WHEN coalesce(a.win_cnt, 0) = 0                               THEN 'legacy_before_attribution'
         WHEN a.by_sampled                                             THEN 'backfill_sample_time'
         ELSE 'backfill_receive_time_estimated'
       END AS attribution_reason,
       CASE
         WHEN t.company_id IS NULL                                    THEN NULL
         WHEN coalesce(t.sampled_at, t."timestamp") IS NULL            THEN NULL
         WHEN t.sampled_at IS NOT NULL
              AND abs(extract(epoch from (t.sampled_at - t."timestamp"))) > %(max_skew)s
                                                                      THEN NULL
         WHEN coalesce(a.win_cnt, 0) <> 1                              THEN NULL
         ELSE a.bid
       END AS binding_id,
       CASE
         WHEN t.company_id IS NULL                                    THEN NULL
         WHEN coalesce(t.sampled_at, t."timestamp") IS NULL            THEN NULL
         WHEN t.sampled_at IS NOT NULL
              AND abs(extract(epoch from (t.sampled_at - t."timestamp"))) > %(max_skew)s
                                                                      THEN NULL
         WHEN coalesce(a.win_cnt, 0) <> 1                              THEN NULL
         ELSE a.bv
       END AS binding_version,
       t.created_at
  FROM telemetry t
  LEFT JOIN agg a ON a.tid = t.id
 WHERE t.attribution_status IS NULL
 ORDER BY t.created_at
"""

TRUSTED_REASON = "backfill_sample_time"


def db_name(dsn: str) -> str:
    return dsn.rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def fetch_decisions(conn, max_skew, limit):
    sql = CLASSIFY_SQL + (" LIMIT %(limit)s" if limit else "")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, {"max_skew": max_skew, "limit": limit})
        return cur.fetchall()


def summarize(rows):
    """返回 OrderedDict{(status, reason): count}"""
    out = OrderedDict()
    for r in rows:
        key = (r["attribution_status"], r["attribution_reason"])
        out[key] = out.get(key, 0) + 1
    return out


def print_report(rows, max_skew, limit, only_trusted, dbname, applied=False):
    print("=" * 72)
    print(f"004 归属回填{'（已写入）' if applied else '（DRY-RUN，未写入）'}")
    print(f"  库={dbname}  最大允许偏差={max_skew}s  limit={limit or '不限'}  "
          f"only-trusted={'是' if only_trusted else '否'}")
    print(f"  范围内行数 = {len(rows)}")
    print("=" * 72)
    print(f"{'status':<10} {'reason':<34} {'行数':>8}")
    print("-" * 72)
    for (st, rs), n in sorted(summarize(rows).items(), key=lambda kv: -kv[1]):
        print(f"{st:<10} {rs:<34} {n:>8}")
    print("-" * 72)


def print_samples(rows, n):
    if not n or not rows:
        return
    print(f"\n抽样（前 {min(n, len(rows))} 条）：")
    for r in rows[:n]:
        skew = ""
        if r["sample_time"] and r["receive_time"]:
            skew = f"skew={(r['sample_time'] - r['receive_time']).total_seconds():.1f}s"
        print(f"  {r['msg_id']!r:<20} recv={r['receive_time']} "
              f"sampled={r['sample_time']} tq={r['time_quality']} {skew} "
              f"-> {r['attribution_status']}/{r['attribution_reason']}")


def apply_rows(conn, rows, out_dir, quiet=False):
    """按分级结果写库；返回写入条数。"""
    if not rows:
        return 0
    touched = []
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                'UPDATE telemetry SET attribution_status=%s, attribution_reason=%s, '
                'binding_id=%s, binding_version=%s WHERE id=%s AND attribution_status IS NULL',
                (r["attribution_status"], r["attribution_reason"],
                 r["binding_id"], r["binding_version"], r["id"]))
            if cur.rowcount:
                touched.append(r["id"])
    conn.commit()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(out_dir, exist_ok=True)
    manifest = os.path.join(out_dir, f"backfill-attribution-{ts}.json")
    rollback = os.path.join(out_dir, f"backfill-attribution-{ts}.rollback.sql")
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"written_at": ts, "count": len(touched), "ids": touched,
                   "by_reason": {f"{k[0]}/{k[1]}": v for k, v in summarize(rows).items()}},
                  f, ensure_ascii=False, indent=2)
    with open(rollback, "w", encoding="utf-8") as f:
        f.write("-- 004 归属回填回滚：这行改动前 attribution_status 均为 NULL，\n"
                "-- 因此回滚就是把 manifest 里的 id 恢复成 NULL（连同 reason/binding 一起清掉）。\n"
                f"-- manifest: {manifest}\n"
                f"-- 行数: {len(touched)}\n\n"
                "BEGIN;\n\n")
        if touched:
            vals = ",".join("'" + i.replace("'", "''") + "'" for i in touched)
            f.write("UPDATE telemetry\n"
                    "   SET attribution_status = NULL,\n"
                    "       attribution_reason = NULL,\n"
                    "       binding_id         = NULL,\n"
                    "       binding_version    = NULL\n"
                    f" WHERE id IN ({vals});\n\n")
        id_list = ",".join("'" + i.replace("'", "''") + "'" for i in touched) or "''"
        f.write("-- 校验：下面这条应为 0\n"
                "SELECT count(*) AS still_marked FROM telemetry\n"
                f" WHERE id IN ({id_list})\n"
                "   AND attribution_status IS NOT NULL;\n\n"
                "COMMIT;\n")
    if not quiet:
        print(f"\n已写入 {len(touched)} 行（并发跳过 {len(rows) - len(touched)} 行）")
        print(f"manifest: {manifest}")
        print(f"回滚 SQL: {rollback}")
    return len(touched), manifest, rollback


def main():
    ap = argparse.ArgumentParser(description="004 telemetry 归属回填（默认 dry-run）")
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_SYNC_URL", ""),
                    help="同步 DSN（postgresql://...）；默认取环境变量 DATABASE_SYNC_URL")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="只读预演（默认）")
    ap.add_argument("--apply", action="store_true",
                    help="实际写库（仅测试库允许）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 行（按 created_at）")
    ap.add_argument("--only-trusted", action="store_true",
                    help="只处理可信档（resolved/backfill_sample_time），推定与 pending 一律不动")
    ap.add_argument("--max-skew", type=int, default=300,
                    help="sampled_at 与接收时刻的最大允许偏差（秒），超了判 pending（默认 300）")
    ap.add_argument("--sample", type=int, default=0, help="打印前 N 条抽样明细")
    ap.add_argument("--out-dir", default="/opt/greenhouse-nightly/logs",
                    help="manifest / 回滚 SQL 输出目录")
    ap.add_argument("--json", action="store_true",
                    help="只输出 JSON（供脚本/测试解析），不打印人类可读报表")
    args = ap.parse_args()

    if not args.db_url:
        print("缺少 DSN：用 --db-url 或设 DATABASE_SYNC_URL", file=sys.stderr)
        return 2
    dbname = db_name(args.db_url)
    if args.apply and TEST_DB_MARKER not in dbname:
        print(f"拒绝写库：--apply 只允许对 {TEST_DB_MARKER} 执行，当前库={dbname}",
              file=sys.stderr)
        return 3

    conn = psycopg2.connect(args.db_url)
    try:
        # 只读会话只在 dry-run 时开；--apply 走普通会话（且只允许测试库，已在上一步校验）
        if not args.apply:
            conn.set_session(readonly=True, autocommit=False)
        rows = fetch_decisions(conn, args.max_skew, args.limit)
        if args.only_trusted:
            rows = [r for r in rows if r["attribution_reason"] == TRUSTED_REASON]
        if args.json:
            print(json.dumps({
                "db": dbname, "max_skew": args.max_skew, "limit": args.limit,
                "only_trusted": args.only_trusted, "in_scope": len(rows),
                "by_outcome": {f"{k[0]}/{k[1]}": v for k, v in summarize(rows).items()},
                "rows": [{"id": r["id"], "msg_id": r["msg_id"],
                          "status": r["attribution_status"],
                          "reason": r["attribution_reason"]} for r in rows],
            }, ensure_ascii=False))
        else:
            print_report(rows, args.max_skew, args.limit, args.only_trusted, dbname)
            print_samples(rows, args.sample)
        if args.apply:
            if TEST_DB_MARKER not in dbname:
                print("拒绝写库（二次校验）", file=sys.stderr)
                return 3
            n, manifest, rollback = apply_rows(conn, rows, args.out_dir, quiet=args.json)
            if args.json:
                print(json.dumps({"written": n, "in_scope": len(rows),
                                  "manifest": manifest, "rollback_sql": rollback},
                                 ensure_ascii=False))
            else:
                print_report(rows, args.max_skew, args.limit, args.only_trusted,
                             dbname, applied=True)
        elif not args.json:
            print("\nDRY-RUN：未写入任何数据。加 --apply 才会写（且只对测试库生效）。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
