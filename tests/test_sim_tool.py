"""T5 模拟器自检：把 scripts/sim_device_check.py 的 14 个场景也纳入 pytest。

目的：模拟器不是"跑过一次就算"，每次全量测试都要证明它自己在工作；
正向场景必须真的进库，负向场景必须真的被服务端拒绝。
"""
import pytest

from tests.conftest import ROOT  # noqa: F401  保证 ROOT 在 sys.path 里


@pytest.mark.asyncio
async def test_sim_tool_all_scenarios_pass():
    from scripts.sim_device_check import run_all

    results = await run_all(verbose=False)
    failed = [r for r in results if not r["ok"]]
    assert not failed, "模拟器有场景未通过：" + str(
        [(r["name"], r["detail"]) for r in failed])
    kinds = {r["kind"] for r in results}
    assert {"正向", "负向"} <= kinds, "模拟场景必须同时包含正向与负向（故意构造的错误）"
    assert len(results) >= 14
