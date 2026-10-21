#!/usr/bin/env python3
"""原始报文（raw_messages）保留期清理脚本。

默认**只统计不删除**（dry-run）。加 --apply 才真正删除。

保留策略（可用环境变量覆盖）：
    RAW_RETENTION_DAYS = 90    超过 N 天的报文
    RAW_MAX_BYTES      = 20GB  总量超过上限时，从最旧的开始删到上限内

安全护栏（重要）：
    脚本启动即校验 DATABASE_URL 必须指向 **测试库 greenhouse_nightly_test**，
    否则直接拒绝运行。本脚本**不在生产库执行**。

用法：
    python scripts/purge_raw_messages.py                 # 只看会删多少
    python scripts/purge_raw_messages.py --apply         # 真正删除
    RAW_RETENTION_DAYS=7 python scripts/purge_raw_messages.py --apply
"""
import argparse
import os
import sys

TEST_DB_MARKER = "greenhouse_nightly_test"


def _parse_size(text: str) -> int:
    text = (text or "").strip().lower()
    if not text:
        return 0
    units = {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4}
    for suffix, mult in units.items():
        if text.endswith(suffix):
            return int(float(text[:-len(suffix)]) * mult)
    return int(float(text))


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


async def main_async(days: int, max_bytes: int, apply: bool) -> int:
    import asyncio
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.environ.get("DATABASE_URL", "")
    if TEST_DB_MARKER not in url:
        print(f"拒绝运行：DATABASE_URL 未指向 {TEST_DB_MARKER}（当前={url!r}）", file=sys.stderr)
        return 2

    engine = create_async_engine(url)
    try:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as db:
            total_rows, total_bytes = (await db.execute(
                text("SELECT count(*), coalesce(sum(payload_bytes),0) FROM raw_messages")
            )).one()
            old_rows, old_bytes = (await db.execute(
                text("SELECT count(*), coalesce(sum(payload_bytes),0) FROM raw_messages"
                     " WHERE received_at < now() - (:d || ' days')::interval"),
                {"d": str(days)})).one()
            print(f"当前报文：{total_rows} 条 / {human(int(total_bytes))}")
            print(f"超过 {days} 天：{old_rows} 条 / {human(int(old_bytes))}")
            print(f"容量上限：{human(max_bytes)}")

            overflow = max(int(total_bytes) - max_bytes, 0)
            if overflow > 0:
                rows = (await db.execute(
                    text("SELECT count(*) FROM ("
                         "  SELECT received_at, payload_bytes,"
                         "         sum(payload_bytes) OVER (ORDER BY received_at)"
                         "           AS running FROM raw_messages"
                         " ) s WHERE s.running <= :lim"),
                    {"lim": int(overflow)})).scalar() or 0
                print(f"超出容量上限 {human(overflow)}，需再删最旧的 {rows} 条")

            if not apply:
                print("dry-run：未删除任何数据（加 --apply 才会真的删）")
                return 0

            res = await db.execute(
                text("DELETE FROM raw_messages"
                     " WHERE received_at < now() - (:d || ' days')::interval"),
                {"d": str(days)})
            deleted = res.rowcount or 0
            await db.commit()
            print(f"已按时间删除：{deleted} 条")

            if int(total_bytes) - int(old_bytes) > max_bytes:
                res2 = await db.execute(
                    text("DELETE FROM raw_messages WHERE id IN ("
                         "  SELECT id FROM ("
                         "    SELECT id, sum(payload_bytes) OVER (ORDER BY received_at)"
                         "      AS running FROM raw_messages"
                         "  ) s WHERE s.running <= :lim)"),
                    {"lim": int(int(total_bytes) - int(old_bytes) - max_bytes)})
                await db.commit()
                print(f"已按容量再删除：{res2.rowcount or 0} 条")

            remain = (await db.execute(
                text("SELECT count(*) FROM raw_messages"))).scalar() or 0
            print(f"剩余报文：{remain} 条")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认 dry-run）")
    args = ap.parse_args()
    days = int(os.environ.get("RAW_RETENTION_DAYS", "90"))
    max_bytes = _parse_size(os.environ.get("RAW_MAX_BYTES", "20GB"))
    import asyncio
    return asyncio.run(main_async(days, max_bytes, args.apply))


if __name__ == "__main__":
    sys.exit(main())
