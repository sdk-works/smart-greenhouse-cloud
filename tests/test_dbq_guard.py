"""T7：dbq.sh 只读守卫的自测（重点：不再误伤 created_at / updated_at）。

守卫的正确性两头都要钉住：
  * 只读查询**不能被拒**（旧版把 created_at / updated_at 当写操作，踩过坑）；
  * 写操作**必须被拒**（含 `select 1; drop table x` 这种多语句注入）。
"""
import os
import subprocess

GUARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "dbq.sh")

# 不真的连库：只要守卫放行就会去执行 psql，这里用 REJECTED 与否判断即可。
# 为了不依赖生产库可用性，用环境变量把 psql 换成一个回显命令。
ENV = dict(os.environ)
ENV["PATH"] = "/nonexistent-bin:" + ENV.get("PATH", "")


def run(sql):
    """返回 (rc, stdout+stderr)。rc=2 表示被守卫拒绝；非 2 表示放行。"""
    p = subprocess.run(["bash", GUARD, sql], capture_output=True, text=True,
                       env=ENV, timeout=30)
    return p.returncode, (p.stdout + p.stderr)


def test_readonly_queries_with_time_columns_are_allowed():
    """旧版在这里会误拒：created_at / updated_at 含 create / update 子串。"""
    for sql in (
        "select created_at from telemetry order by created_at desc limit 3",
        "select updated_at, created_at from devices limit 1",
        "select count(*) from telemetry where created_at > now() - interval '1 day'",
        "select id, created_at from raw_messages where updated_at is null limit 1",
        "with t as (select created_at from telemetry) select count(*) from t",
        "\\d telemetry",
        "show search_path",
    ):
        rc, out = run(sql)
        assert rc != 2, f"只读查询被误拒：{sql}\n{out}"


def test_writing_statements_are_rejected():
    for sql in (
        "insert into telemetry (id) values ('x')",
        "update telemetry set attribution_status = 'pending'",
        "delete from telemetry where id = 'x'",
        "drop table telemetry",
        "alter table telemetry add column x int",
        "truncate telemetry",
        "create table t (id int)",
        "grant all on telemetry to public",
        "vacuum full telemetry",
        "copy telemetry from stdin",
    ):
        rc, out = run(sql)
        assert rc == 2, f"写操作没被拒：{sql}\n{out}"
        assert "REJECTED" in out


def test_multistatement_injection_is_rejected():
    """`select 1; drop table x` 必须被拒 —— 只判首句是不够的。"""
    rc, out = run("select 1; drop table telemetry")
    assert rc == 2, out
    rc, out = run("select * from telemetry; update telemetry set id='x'")
    assert rc == 2, out


def test_non_readonly_leading_keyword_is_rejected():
    rc, out = run("do $$ begin end $$")
    assert rc == 2, out
