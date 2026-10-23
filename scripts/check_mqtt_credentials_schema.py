#!/usr/bin/env python3
"""006 迁移前置核对：`mqtt_credentials` 的**列比对**（只读）。

为什么要有这个脚本：生产库里 `mqtt_credentials` **在 006 之前就存在**
（2026-10-22 手工建表）。006 全是 `IF NOT EXISTS`，所以直接跑不会报错，
但也不会告诉你"生产这张表到底缺哪些列、类型对不对、现有数据会不会踩坑"。
上线前必须先比对，不能只看"迁移没报错"。

检查四项：
  1. 表是否存在
  2. 006 需要的列是否齐全（缺哪几列）
  3. 已有列的类型 / NOT NULL 与 006 的 `CREATE TABLE` 声明是否一致
  4. 数据面：现有凭据的 device_id 能不能在 devices.device_id 里对上
     （对不上说明 device_id 存的不是设备身份，入网接口会查不到）

用法（生产只读，不写）：
  python3 scripts/check_mqtt_credentials_schema.py --db-url "$DATABASE_SYNC_URL"
"""
import argparse
import os
import sys

import psycopg2
import psycopg2.extras

# 与 migrations/006_device_onboarding_up.sql 的 CREATE TABLE 声明保持一致
EXPECTED_COLUMNS = {
    "username":      ("character varying", "NO"),
    "password_hash": ("text", "NO"),
    "clientid":      ("character varying", "NO"),
    "device_id":     ("character varying", "NO"),
    "enabled":       ("boolean", "NO"),
    "note":          ("text", "YES"),
    "created_at":    ("timestamp with time zone", "NO"),
}
# 006 新增列（ADD COLUMN IF NOT EXISTS）
ADDED_COLUMNS = ["asset_no", "compat_identity", "updated_at", "disabled_at",
                 "last_rotated_at", "rotation_count", "created_by", "updated_by"]
EXPECTED_INDEXES = ["ux_mqtt_credentials_device", "ix_mqtt_credentials_enabled"]


def db_name(dsn):
    return dsn.rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_SYNC_URL", ""))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not a.db_url:
        print("缺少 DSN：用 --db-url 或设 DATABASE_SYNC_URL", file=sys.stderr)
        return 2

    dsn, name = a.db_url, db_name(a.db_url)
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=False)
    out = {"db": name, "problems": [], "warnings": []}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.mqtt_credentials') AS t")
            if cur.fetchone()["t"] is None:
                out["table_exists"] = False
                out["problems"].append("表 mqtt_credentials 不存在（006 会新建，安全）")
            else:
                out["table_exists"] = True
                cur.execute("""SELECT column_name, data_type, is_nullable
                                 FROM information_schema.columns
                                WHERE table_name='mqtt_credentials'""")
                cols = {r["column_name"]: r for r in cur.fetchall()}
                out["columns_present"] = sorted(cols)

                missing = [c for c in EXPECTED_COLUMNS if c not in cols]
                out["missing_columns"] = missing
                if missing:
                    out["problems"].append(f"缺列：{', '.join(missing)}")

                # 类型/NOT NULL 差异：IF NOT EXISTS 不会去修，需要单独 ALTER，
                # 但不影响 006 能否跑通，所以记为 warning 而不是 problem
                for c, (dt, nn) in EXPECTED_COLUMNS.items():
                    if c in cols and (cols[c]["data_type"] != dt
                                      or cols[c]["is_nullable"] != nn):
                        out["warnings"].append(
                            f"列 {c} 与 006 声明不一致：期望 {dt}/"
                            f"{'NOT NULL' if nn=='NO' else 'NULL'}，实际 "
                            f"{cols[c]['data_type']}/"
                            f"{'NOT NULL' if cols[c]['is_nullable']=='NO' else 'NULL'}"
                            f"（IF NOT EXISTS 不会修，需单独 ALTER）")

                out["missing_006_columns"] = [c for c in ADDED_COLUMNS if c not in cols]

                cur.execute("""SELECT indexname FROM pg_indexes
                                WHERE tablename='mqtt_credentials'""")
                idx = {r["indexname"] for r in cur.fetchall()}
                out["missing_indexes"] = [i for i in EXPECTED_INDEXES if i not in idx]

                cur.execute("SELECT count(*) AS n FROM mqtt_credentials")
                out["rows"] = cur.fetchone()["n"]
                cur.execute("""SELECT c.username, c.clientid, c.device_id, c.enabled,
                                      (d.id IS NULL) AS device_not_found
                                 FROM mqtt_credentials c
                                 LEFT JOIN devices d ON d.device_id = c.device_id""")
                out["credentials"] = [dict(r) for r in cur.fetchall()]
                if any(r["device_not_found"] for r in out["credentials"]):
                    out["problems"].append(
                        "有凭据的 device_id 在 devices.device_id 里找不到 —— 入网接口按设备身份查询会落空")
    finally:
        conn.close()

    if a.json:
        import json
        print(__import__("json").dumps(out, ensure_ascii=False, default=str, indent=2))
    else:
        print(f"库={out['db']}")
        print(f"表存在        : {out.get('table_exists')}")
        if out.get("table_exists"):
            print(f"现有列        : {', '.join(out['columns_present'])}")
            print(f"缺列          : {out['missing_columns'] or '无'}")
            print(f"006 待补列    : {out['missing_006_columns'] or '无'}")
            print(f"缺索引        : {out['missing_indexes'] or '无'}")
            print(f"凭据行数      : {out['rows']}")
            for c in out["credentials"]:
                print(f"  - username={c['username']} clientid={c['clientid']} "
                      f"device_id={c['device_id']} enabled={c['enabled']} "
                      f"设备对不上={c['device_not_found']}")
        print(f"\n阻断问题 {len(out['problems'])} 条：")
        for p in out["problems"]:
            print(f"  ! {p}")
        if not out["problems"]:
            print("  （无，006 可以安全执行）")
        print(f"\n警告 {len(out['warnings'])} 条：")
        for w in out["warnings"]:
            print(f"  - {w}")
        if not out["warnings"]:
            print("  （无）")
    return 1 if out["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
