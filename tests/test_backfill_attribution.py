"""T3：004 归属回填脚本的测试库演练（与生产同构的样本 + 边界）。

覆盖七种可复现场景，外加 --dry-run / --limit / --only-trusted / --apply / 回滚 五种用法。
生产只做只读预演（见 docs/attribution-backfill-20261023.md），本文件**只连测试库**。
"""
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.models import DeviceType, Telemetry

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "backfill_attribution.py")
TEST_DB_MARKER = "greenhouse_nightly_test"


def sync_dsn() -> str:
    """从 settings 的异步 DSN 推出同步 DSN（asyncpg -> psycopg2）。"""
    from app.core.config import settings
    url = settings.DATABASE_URL
    assert TEST_DB_MARKER in url, f"测试必须连 {TEST_DB_MARKER}"
    return url.replace("postgresql+asyncpg://", "postgresql://")


def run_script(*args, expect_rc=0):
    proc = subprocess.run([sys.executable, SCRIPT, "--db-url", sync_dsn(), *args],
                          capture_output=True, text=True)
    assert proc.returncode == expect_rc, f"脚本退出码 {proc.returncode}\n{proc.stderr}"
    return proc


def run_json(*args):
    proc = run_script("--json", *args)
    # --apply 时会额外打一行 JSON，取最后一行
    return json.loads(proc.stdout.strip().splitlines()[-1])


async def mk_world(fx, db, suffix="", t0=None):
    cid = await fx.company(db, f"BF-C{suffix}")
    gh = await fx.greenhouse(db, cid, f"BF-GH{suffix}")
    dev = await fx.device(db, gh, DeviceType.SENSOR, f"BF-DEV{suffix}")
    t0 = t0 or (datetime.now(timezone.utc) - timedelta(days=3))
    b = await fx.binding(db, dev, gh, version=1, start_at=t0)   # end_at=NULL，覆盖 t0 之后
    await db.flush()
    return cid, gh, dev, b, t0


async def mk_tel(fx, db, gh, dev, cid, msg_id, recv, sampled=None, tq=0):
    tid = str(uuid.uuid4())
    t = Telemetry(id=tid, device_id=dev.id, greenhouse_id=gh.id,
                  company_id=cid, timestamp=recv, sampled_at=sampled,
                  time_quality=tq, data_type="telemetry", msg_id=msg_id,
                  temperature=20.0, attribution_status=None)
    db.add(t)
    fx.tel_rows.append(tid)
    await db.flush()
    return tid


@pytest.mark.asyncio
async def test_classification_matches_expected(iso):
    """七种场景各归各位；护栏把"时钟错一年"的行挡在可信之外。"""
    fx, db = iso
    cid, gh, dev, _b, t0 = await mk_world(fx, db, "-A")

    # 1) 窗口内 + 采集时间可信（偏差 1s）      -> resolved / backfill_sample_time
    r1_recv = t0 + timedelta(hours=1)
    r1 = await mk_tel(fx, db, gh, dev, cid, "BF/1/1", r1_recv,
                      sampled=r1_recv - timedelta(seconds=1), tq=1)
    # 2) 窗口内 + 只有接收时间                 -> resolved / backfill_receive_time_estimated
    r2 = await mk_tel(fx, db, gh, dev, cid, "BF/1/2", t0 + timedelta(hours=2))
    # 3) 窗口内 + sampled_at 错一年（护栏命中） -> pending / sample_time_implausible
    r3_recv = t0 + timedelta(hours=3)
    r3 = await mk_tel(fx, db, gh, dev, cid, "BF/1/3", r3_recv,
                      sampled=r3_recv - timedelta(days=365), tq=1)
    # 4) 早于绑定窗口起点（无窗口覆盖）         -> pending / legacy_before_attribution
    r4 = await mk_tel(fx, db, gh, dev, cid, "BF/1/4", t0 - timedelta(hours=1))
    # 5) 无 company_id                         -> pending / no_company
    r5 = await mk_tel(fx, db, gh, dev, None, "BF/1/5", t0 + timedelta(hours=5))

    # 6) 另一台设备：两个重叠窗口               -> pending / conflict_binding_window
    cid2, gh2, dev2, _b2, t0_2 = await mk_world(fx, db, "-B")
    await fx.binding(db, dev2, gh2, version=2, start_at=t0_2,
                     end_at=datetime.now(timezone.utc) + timedelta(days=1))
    r6 = await mk_tel(fx, db, gh2, dev2, cid2, "BF/2/1", t0_2 + timedelta(hours=1))

    # 7) 未绑定设备的棚（覆盖窗口 = 0）          -> pending / legacy_before_attribution
    gh3 = await fx.greenhouse(db, cid, "BF-GH-NOBIND")
    dev3 = await fx.device(db, gh3, DeviceType.SENSOR, "BF-DEV-NOBIND")
    await db.flush()
    r7 = await mk_tel(fx, db, gh3, dev3, cid, "BF/3/1", t0 + timedelta(hours=1))
    await db.commit()

    out = run_json("--dry-run")
    by = dict(out["by_outcome"])
    rowmap = {r["id"]: f"{r['status']}/{r['reason']}" for r in out["rows"]}

    assert rowmap[r1] == "resolved/backfill_sample_time", rowmap[r1]
    assert rowmap[r2] == "resolved/backfill_receive_time_estimated", rowmap[r2]
    assert rowmap[r3] == "pending/sample_time_implausible", rowmap[r3]
    assert rowmap[r4] == "pending/legacy_before_attribution", rowmap[r4]
    assert rowmap[r5] == "pending/no_company", rowmap[r5]
    assert rowmap[r6] == "pending/conflict_binding_window", rowmap[r6]
    assert rowmap[r7] == "pending/legacy_before_attribution", rowmap[r7]

    # 汇总口径：可信 1 / 推定 1 / pending 5
    assert by.get("resolved/backfill_sample_time") == 1
    assert by.get("resolved/backfill_receive_time_estimated") == 1
    assert sum(v for k, v in by.items() if k.startswith("pending/")) == 5


@pytest.mark.asyncio
async def test_guard_is_tunable(iso):
    """护栏阈值可调：放大到 400 天，"错一年"那行会被放行（说明护栏真的在起作用）。"""
    fx, db = iso
    # 绑定窗口起点前移到 400 天前，这样"错一年"的 sampled_at 仍在窗口内，
    # 唯一决定它命运的就是护栏本身（而不是"窗口没覆盖"）。
    t0 = datetime.now(timezone.utc) - timedelta(days=400)
    cid, gh, dev, _b, t0 = await mk_world(fx, db, "-G", t0=t0)
    recv = t0 + timedelta(days=365)
    rid = await mk_tel(fx, db, gh, dev, cid, "BF/9/1", recv,
                       sampled=recv - timedelta(days=365), tq=1)
    await db.commit()

    strict = run_json("--dry-run", "--max-skew", "300")
    assert {x["id"]: x["reason"] for x in strict["rows"]}[rid] == "sample_time_implausible"
    loose = run_json("--dry-run", "--max-skew", "40000000")
    assert {x["id"]: x["reason"] for x in loose["rows"]}[rid] == "backfill_sample_time"


@pytest.mark.asyncio
async def test_only_trusted_and_limit(iso):
    """--only-trusted 只留可信档；--limit 限制范围内行数。"""
    fx, db = iso
    cid, gh, dev, _b, t0 = await mk_world(fx, db, "-L")
    recv = t0 + timedelta(hours=1)
    await mk_tel(fx, db, gh, dev, cid, "BF/8/1", recv,
                 sampled=recv - timedelta(seconds=1), tq=1)      # 可信
    await mk_tel(fx, db, gh, dev, cid, "BF/8/2", t0 + timedelta(hours=2))   # 推定
    await db.commit()

    allrows = run_json("--dry-run")
    assert allrows["in_scope"] == 2
    trusted = run_json("--dry-run", "--only-trusted")
    assert trusted["in_scope"] == 1
    assert trusted["rows"][0]["reason"] == "backfill_sample_time"
    assert run_json("--dry-run", "--limit", "1")["in_scope"] == 1


@pytest.mark.asyncio
async def test_apply_writes_and_rollback_restores(iso):
    """--apply 写库 -> 断言落库 -> 执行生成的回滚 SQL -> 断言恢复成 NULL。"""
    fx, db = iso
    cid, gh, dev, _b, t0 = await mk_world(fx, db, "-R")
    recv = t0 + timedelta(hours=1)
    r_trusted = await mk_tel(fx, db, gh, dev, cid, "BF/7/1", recv,
                             sampled=recv - timedelta(seconds=1), tq=1)
    r_est = await mk_tel(fx, db, gh, dev, cid, "BF/7/2", t0 + timedelta(hours=2))
    r_pend = await mk_tel(fx, db, gh, dev, cid, "BF/7/3", t0 - timedelta(hours=1))
    await db.commit()
    ids = [r_trusted, r_est, r_pend]

    out = run_json("--apply")
    assert out["written"] == 3, out
    rollback_sql = out["rollback_sql"]
    assert os.path.exists(rollback_sql)

    async with AsyncSessionLocal() as db2:
        rows = (await db2.execute(
            select(Telemetry.id, Telemetry.attribution_status, Telemetry.attribution_reason)
            .where(Telemetry.id.in_(ids)))).all()
        got = {r[0]: (r[1], r[2]) for r in rows}
    assert got[r_trusted] == ("resolved", "backfill_sample_time")
    assert got[r_est] == ("resolved", "backfill_receive_time_estimated")
    assert got[r_pend] == ("pending", "legacy_before_attribution")

    # 用 psycopg2 直接执行生成的回滚 SQL（不依赖 sudo/psql）
    import psycopg2
    with open(rollback_sql, encoding="utf-8") as f:
        sql = f.read()
    conn = psycopg2.connect(sync_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()

    async with AsyncSessionLocal() as db2:
        rows = (await db2.execute(
            select(Telemetry.id, Telemetry.attribution_status,
                   Telemetry.attribution_reason, Telemetry.binding_id)
            .where(Telemetry.id.in_(ids)))).all()
        after = {r[0]: (r[1], r[2], r[3]) for r in rows}
    for i in ids:
        assert after[i] == (None, None, None), after[i]


@pytest.mark.asyncio
async def test_only_trusted_leaves_estimated_untouched(iso):
    """--apply --only-trusted：推定档必须一行都不动（这是最容易被写错的一条）。"""
    fx, db = iso
    cid, gh, dev, _b, t0 = await mk_world(fx, db, "-T")
    recv = t0 + timedelta(hours=1)
    r_trusted = await mk_tel(fx, db, gh, dev, cid, "BF/6/1", recv,
                             sampled=recv - timedelta(seconds=1), tq=1)
    r_est = await mk_tel(fx, db, gh, dev, cid, "BF/6/2", t0 + timedelta(hours=2))
    await db.commit()

    out = run_json("--apply", "--only-trusted")
    assert out["written"] == 1

    async with AsyncSessionLocal() as db2:
        got = dict((await db2.execute(
            select(Telemetry.id, Telemetry.attribution_status)
            .where(Telemetry.id.in_([r_trusted, r_est])))).all())
    assert got[r_trusted] == "resolved"
    assert got[r_est] is None


def test_refuses_to_write_non_test_database():
    """--apply 对非测试库必须拒绝（生产写保护的第二道闸）。"""
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--db-url",
         "postgresql://u:p@localhost:5432/greenhouse", "--apply"],
        capture_output=True, text=True)
    assert proc.returncode == 3
    assert "拒绝写库" in proc.stderr
