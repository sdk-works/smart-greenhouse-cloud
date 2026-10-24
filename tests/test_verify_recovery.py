"""恢复验收：新 boot_id / seq 重置 / 节律 / 空白不计丢包。

重点不是"能跑"，而是**口径不能错**：
断电期间的空白必须固定记 0 应达、0 丢包，且明确"不计入完整率"，
否则一次计划停机就会把可靠性数字永久弄脏。
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import verify_recovery as vr  # noqa: E402

T0 = datetime(2026, 10, 25, 9, 0, 0, tzinfo=timezone.utc)


def rows(boot, seqs, start=T0, step=60):
    return [(start + timedelta(seconds=i * step), f"JY001/{boot}/{s}")
            for i, s in enumerate(seqs)]


def pre_rows(boot=31, last_seq=922, n=3):
    """断电前基线：boot 31，最后一条 seq 922。"""
    last_at = T0 - timedelta(hours=26)
    return [(last_at - timedelta(seconds=(n - 1 - i) * 60), f"JY001/{boot}/{last_seq - (n - 1 - i)}")
            for i in range(n)]


def check(res, name):
    for c in res["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"没有这条检查：{name}｜已有={[c['name'] for c in res['checks']]}")


def test_normal_recovery_passes_all_checks():
    res = vr.analyze(pre_rows(), rows(32, range(1, 11)), 60)
    assert res["state"] == vr.STATE_RECOVERED
    assert check(res, "新 boot_id")["ok"] is True
    assert check(res, "seq 重新开始")["ok"] is True
    assert check(res, "恢复后 seq 连续")["ok"] is True
    assert check(res, "上传节律")["ok"] is True
    assert res["before"]["boot"] == 31 and res["after"]["boot"] == 32


def test_not_recovered_is_a_state_not_an_error():
    """设备还没上电：必须是"尚未恢复"，不能抛异常也不能假装成功。"""
    res = vr.analyze(pre_rows(), [], 60)
    assert res["state"] == vr.STATE_NOT_YET
    assert res["after"] is None
    assert check(res, "设备已恢复")["ok"] is False
    assert res["accounting"]["dropped_samples"] == 0


def test_same_boot_means_mcu_did_not_restart():
    """boot 不变、seq 续接 → MCU 没重启，要人工确认是否真的断过电。"""
    res = vr.analyze(pre_rows(), rows(31, range(923, 933)), 60)
    assert res["state"] == vr.STATE_PARTIAL
    c = check(res, "新 boot_id")
    assert c["ok"] is False
    assert "没有重启" in c["detail"]


def test_seq_not_starting_at_one_is_flagged():
    res = vr.analyze(pre_rows(), rows(32, range(5, 15)), 60)
    assert check(res, "seq 重新开始")["ok"] is False
    assert res["state"] == vr.STATE_PARTIAL


def test_seq_gap_and_duplicate_are_both_flagged():
    gap = vr.analyze(pre_rows(), rows(32, [1, 2, 3, 9, 10]), 60)
    assert check(gap, "恢复后 seq 连续")["ok"] is False
    assert "跳号" in check(gap, "恢复后 seq 连续")["detail"]

    dup = vr.analyze(pre_rows(), rows(32, [1, 2, 2, 3]), 60)
    assert check(dup, "恢复后 seq 连续")["ok"] is False
    assert "重复" in check(dup, "恢复后 seq 连续")["detail"]


def test_bad_cadence_is_flagged():
    """间隔变成 5s 说明设备在补传或时钟异常，不能当"恢复良好"。"""
    res = vr.analyze(pre_rows(), rows(32, range(1, 11), step=5), 60)
    assert check(res, "上传节律")["ok"] is False
    assert res["cadence"]["median_s"] == 5.0


def test_blank_window_is_never_counted_as_dropped():
    """核心口径：断电空白 → 应达 0 条、丢包 0 条、不计入完整率。"""
    res = vr.analyze(pre_rows(), rows(32, range(1, 11)), 60)
    acc = res["accounting"]
    assert acc["rule"] == "outage_window_excluded"
    assert acc["expected_samples_during_gap"] == 0
    assert acc["dropped_samples"] == 0
    assert acc["counted_in_completeness"] is False
    # 空白时长要如实算出来（26h），但只作为事实记录，不参与任何比例
    assert acc["gap_seconds"] == pytest.approx(26 * 3600, abs=60)


def test_unparsable_msg_id_does_not_crash():
    pre = pre_rows()
    post = [(T0, "garbage"), (T0 + timedelta(seconds=60), "JY001/32/2")]
    res = vr.analyze(pre, post, 60)
    # boot 无法解析 → 判失败但不崩，仍然给出结论
    assert res["state"] == vr.STATE_PARTIAL
    assert res["after"]["boot"] is None


def test_multiple_boots_after_recovery_is_flagged():
    post = rows(32, [1, 2, 3]) + rows(33, [1, 2, 3], start=T0 + timedelta(seconds=180))
    res = vr.analyze(pre_rows(), post, 60)
    assert check(res, "恢复后 boot 一致")["ok"] is False


def test_single_row_after_recovery_has_no_cadence():
    res = vr.analyze(pre_rows(), rows(32, [1]), 60)
    assert res["cadence"] is None
    assert check(res, "上传节律")["ok"] is False


def test_outage_window_comes_from_maintenance_file(tmp_path):
    import json
    p = tmp_path / "mw.json"
    p.write_text(json.dumps({"windows": [
        {"device": "JY001", "start": "2026-10-23T08:54:18+08:00",
         "end": "2026-10-25T18:00:00+08:00"},
        {"device": "JY002", "start": "2026-10-01T00:00:00+08:00",
         "end": "2026-10-02T00:00:00+08:00"},
    ]}), encoding="utf-8")
    s, e = vr.find_outage_window(vr.load_windows(str(p)), "JY001")
    assert s.year == 2026 and s.day == 23
    assert e.day == 25


def test_recovered_but_currently_silent_is_not_reported_healthy():
    """曾恢复过、但现在又静默：不能报"已恢复"，否则让人以为设备正在传。"""
    res = vr.analyze(pre_rows(), rows(32, range(1, 11)), 60,
                     now=T0 + timedelta(hours=30), currently_silent=True)
    assert res["currently_silent"] is True
    assert check(res, "当前在线")["ok"] is False
    assert "当前不在线" in check(res, "当前在线")["detail"]
    assert res["state"] == vr.STATE_PARTIAL


def test_recovered_and_still_streaming_is_healthy():
    res = vr.analyze(pre_rows(), rows(32, range(1, 11)), 60,
                     now=T0 + timedelta(seconds=600), currently_silent=False)
    assert check(res, "当前在线")["ok"] is True
    assert res["state"] == vr.STATE_RECOVERED


def test_ongoing_blank_is_measured_to_now_not_history():
    """断流进行中：空白必须是"最后一条 → 现在"，不能拿历史上那次重启的间隔充数。"""
    res = vr.analyze(pre_rows(), rows(32, range(1, 11)), 60,
                     now=T0 + timedelta(hours=30), currently_silent=True)
    acc = res["accounting"]
    assert acc["gap_ongoing"] is True
    assert acc["gap_seconds"] == pytest.approx(30 * 3600 - 540, abs=1)
    assert acc["dropped_samples"] == 0
    assert acc["counted_in_completeness"] is False


def test_missing_maintenance_file_returns_none(tmp_path):
    assert vr.find_outage_window(vr.load_windows(str(tmp_path / "nope.json")), "JY001") == (None, None)


# ─────────────────────── 断点切分（不能用人工填的起点）───────────────────────
def test_split_finds_last_gap_before_recovery():
    """恢复后：断点必须落在最后一段长间隔上，post 只含恢复后的数据。"""
    pre = pre_rows()                                   # 断电前，间隔 60s
    post = rows(32, range(1, 4), start=T0)             # 恢复后
    allrows = pre + post
    i = vr.split_at_last_gap(allrows, 60)
    assert i == len(pre) - 1
    assert allrows[i][1] == "JY001/31/922"
    assert allrows[i + 1][1] == "JY001/32/1"


def test_split_returns_none_when_outage_not_over():
    """还没上电：断流发生在序列末尾之后，内部没有长间隔 → 不能切出 post。

    这正是之前的 bug：拿"最后一条遥测时刻"当起点，会把断流前那条正常数据
    当成恢复首条，于是判成"已恢复、boot 没变"。
    """
    assert vr.split_at_last_gap(pre_rows(), 60) is None


def test_split_picks_the_most_recent_gap():
    """多次断流时只看最近一次。"""
    a = rows(31, [1, 2, 3])
    b = rows(32, [1, 2, 3], start=a[-1][0] + timedelta(hours=5))
    c = rows(33, [1, 2, 3], start=b[-1][0] + timedelta(hours=7))
    i = vr.split_at_last_gap(a + b + c, 60)
    assert i == len(a + b) - 1
    assert (a + b + c)[i + 1][1] == "JY001/33/1"


# ─────────────────── 断电窗口内"不该有数据"（2026-10-25 夜间补）───────────────────
# 起因：docstring 承诺"窗口里冒出遥测必须逐条列出来"，但代码那一节只算了空白口径，
#       检查**从来没实现** —— 演练场景 C（窗口内塞 3 条）跑出来是"全部通过"。
#       这类"文档说有、代码没有"比没有检查更危险：它会让人以为已经验过。

def _pre_with_inwindow(n=3, window_start=None):
    """断电前的正常数据 + n 条落在窗口内的疑似数据。"""
    base = pre_rows()                                  # boot 31 ... 922
    ws = window_start or (base[-1][0] + timedelta(minutes=2))
    inwin = [(ws + timedelta(seconds=60 * (i + 1)), f"JY001/31/{930 + i}")
             for i in range(n)]
    return base + inwin


def test_in_window_rows_are_flagged_not_swallowed():
    ws = T0 - timedelta(hours=20)
    res = vr.analyze(_pre_with_inwindow(3, ws), rows(32, range(1, 6)), 60,
                     window_start=ws)
    c = check(res, "断电窗口内无数据")
    assert c["ok"] is False
    assert "3 条" in c["detail"]
    assert res["window_check"]["rows_in_window"] == 3
    # 逐条样例也必须在摘要里出现，方便人工定位


def test_clean_window_passes():
    ws = T0 - timedelta(hours=20)
    res = vr.analyze(pre_rows(), rows(32, range(1, 6)), 60, window_start=ws)
    assert check(res, "断电窗口内无数据")["ok"] is True
    assert res["window_check"]["rows_in_window"] == 0


def test_without_window_start_it_says_unknown_not_pass():
    """没给起点就是不判定 —— 不许默认通过，也不许假装没问题。"""
    res = vr.analyze(_pre_with_inwindow(3), rows(32, range(1, 6)), 60)
    wc = res["window_check"]
    assert wc["verified"] is False
    assert wc["start"] is None
    with pytest.raises(AssertionError):
        check(res, "断电窗口内无数据")
