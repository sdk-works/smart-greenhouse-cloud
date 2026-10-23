"""T4：悬挂 job 清理工具的测试库演练。

造一组同构数据（正常 queued / 终态矛盾 / 孤儿 / 超时），验证：
  * 三条识别口径各自命中；
  * **正常 queued 一条都不被动**（这是最容易写错的一条）；
  * --apply 后收敛（再跑一次 dry-run 命中 0）；
  * --dry-run 默认不写库；
  * --apply 对非测试库拒绝。
"""
import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

SCRIPT = "scripts/stuck_job_sweeper.py"
TEST_DB_MARKER = "greenhouse_nightly_test"
TURN = "sweep_turns"
JUDGE = "sweep_judges"


def sync_dsn() -> str:
    from app.core.config import settings
    url = settings.DATABASE_URL
    assert TEST_DB_MARKER in url
    return url.replace("postgresql+asyncpg://", "postgresql://")


def run(*args, expect_rc=0):
    p = subprocess.run([sys.executable, SCRIPT, "--db-url", sync_dsn(), *args],
                       capture_output=True, text=True)
    assert p.returncode == expect_rc, f"rc={p.returncode}\n{p.stderr}"
    return p


def run_json(*args):
    return json.loads(run("--json", *args).stdout.strip().splitlines()[-1])


@pytest.fixture
def tables():
    """建两张同构表，塞入固定样本，测试结束整表删除。"""
    dsn = sync_dsn()
    conn = psycopg2.connect(dsn)
    now = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE {TURN} (
                    id text PRIMARY KEY,
                    status text NOT NULL,
                    updated_at timestamptz NOT NULL DEFAULT now()
                )""")
            cur.execute(f"""
                CREATE TABLE {JUDGE} (
                    id text PRIMARY KEY,
                    turn_id text,
                    status text NOT NULL,
                    last_error text,
                    updated_at timestamptz NOT NULL DEFAULT now()
                )""")
            turn = lambda tid, st: (tid, st, now)          # noqa: E731
            judge = lambda jid, tid, st, ts: (jid, tid, st, None, ts)   # noqa: E731
            rows_turn = [
                turn("T-DONE-1", "completed"),     # 上游完成，下游仍 queued   -> terminal_conflict
                turn("T-DONE-2", "completed"),     # 同上                      -> terminal_conflict
                turn("T-RUN", "running"),          # 上游还在跑，下游 queued    -> 正常，不动
                turn("T-NEW", "pending"),          # 上游还没开始，下游 queued  -> 正常，不动
            ]
            rows_judge = [
                judge("J-CONFLICT-1", "T-DONE-1", "queued", now),
                judge("J-CONFLICT-2", "T-DONE-2", "queued", now),
                judge("J-ORPHAN-1", "T-GONE-1", "queued", now),      # turn 不存在   -> orphan
                judge("J-ORPHAN-2", None, "queued", now),            # ref 为 NULL   -> orphan
                judge("J-NORMAL-1", "T-RUN", "queued", now),         # 正常排队       -> 不动
                judge("J-NORMAL-2", "T-NEW", "queued", now),         # 正常排队       -> 不动
                judge("J-TIMEOUT-1", "T-RUN", "running", now - timedelta(hours=3)),
                judge("J-FRESH-RUN", "T-RUN", "running", now - timedelta(seconds=30)),
            ]
            cur.executemany(f"INSERT INTO {TURN} (id, status, updated_at) VALUES (%s,%s,%s)",
                            rows_turn)
            cur.executemany(f"INSERT INTO {JUDGE} (id, turn_id, status, last_error, updated_at) "
                            f"VALUES (%s,%s,%s,%s,%s)", rows_judge)
        conn.commit()
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {JUDGE}")
            cur.execute(f"DROP TABLE IF EXISTS {TURN}")
        conn.commit()
        conn.close()


def test_detects_three_kinds_and_spares_normal_queued(tables):
    out = run_json("--table", JUDGE, "--ref-table", TURN, "--dry-run",
                   "--timeout-seconds", "3600")
    by_id = {i["job_id"]: i["reason"] for i in out["items"]}
    assert by_id.get("J-CONFLICT-1") == "terminal_conflict"
    assert by_id.get("J-CONFLICT-2") == "terminal_conflict"
    assert by_id.get("J-ORPHAN-1") == "orphan"
    assert by_id.get("J-ORPHAN-2") == "orphan"
    assert by_id.get("J-TIMEOUT-1") == "timeout"
    # 关键：正常 queued 与刚开跑的 running 一律不命中
    assert "J-NORMAL-1" not in by_id
    assert "J-NORMAL-2" not in by_id
    assert "J-FRESH-RUN" not in by_id
    assert out["stuck"] == 5


def test_dry_run_does_not_write(tables):
    run("--table", JUDGE, "--ref-table", TURN, "--dry-run")
    out = run_json("--table", JUDGE, "--ref-table", TURN, "--dry-run")
    assert out["stuck"] == 5          # 还在，说明 dry-run 没动过


def test_apply_converges(tables):
    out = run_json("--table", JUDGE, "--ref-table", TURN, "--apply",
                   "--reason-col", "last_error")
    assert out["cleaned"] == 5
    # 再跑一次：应收敛到 0
    assert run_json("--table", JUDGE, "--ref-table", TURN, "--dry-run")["stuck"] == 0

    conn = psycopg2.connect(sync_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, status, last_error FROM {JUDGE} ORDER BY id")
            got = dict((r[0], (r[1], r[2])) for r in cur.fetchall())
    finally:
        conn.close()
    assert got["J-NORMAL-1"][0] == "queued" and got["J-NORMAL-1"][1] is None
    assert got["J-NORMAL-2"][0] == "queued" and got["J-NORMAL-2"][1] is None
    assert got["J-FRESH-RUN"][0] == "running"
    for jid in ("J-CONFLICT-1", "J-CONFLICT-2", "J-ORPHAN-1", "J-ORPHAN-2", "J-TIMEOUT-1"):
        assert got[jid][0] == "failed"
        assert got[jid][1].startswith("[sweeper] ")


def test_limit_caps_scope(tables):
    out = run_json("--table", JUDGE, "--ref-table", TURN, "--apply", "--limit", "2")
    assert out["cleaned"] == 2


def test_missing_table_reports_instead_of_crashing():
    out = json.loads(run("--json", "--table", "no_such_table_xyz", "--dry-run",
                         expect_rc=4).stdout.strip().splitlines()[-1])
    assert "error" in out and "不存在" in out["error"]


def test_refuses_to_write_non_test_database():
    p = subprocess.run([sys.executable, SCRIPT, "--db-url",
                        "postgresql://u:p@localhost:5432/greenhouse",
                        "--table", JUDGE, "--apply"], capture_output=True, text=True)
    assert p.returncode == 3
    assert "拒绝写库" in p.stderr
