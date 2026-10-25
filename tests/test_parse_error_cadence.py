"""T5-b：解析错误的只读统计口径（scripts/parse_error_cadence.py）。

只测纯函数与 CLI 读文件这一条路径 —— **不依赖 journalctl、不连生产**。
目的是让"1753 条解析错误"这种数字变成可复现、可解释的口径。
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from scripts import parse_error_cadence as pec

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "parse_error_cadence.py"


def log_lines(start, gaps_s, pattern="JSON解析失败"):
    """按间隔序列造日志文本；返回 (文本, 时刻列表)。"""
    t = start
    ts = [t]
    lines = [f"{t.strftime('%Y-%m-%d %H:%M:%S')},307 [WARNING] "
             f"app.mqtt.mqtt_service: [MQTT-V9] {pattern} topic=agri/device/JY001/up"]
    for g in gaps_s:
        t = t + timedelta(seconds=g)
        ts.append(t)
        lines.append(f"{t.strftime('%Y-%m-%d %H:%M:%S')},412 [WARNING] "
                     f"app.mqtt.mqtt_service: [MQTT-V9] {pattern} topic=agri/device/JY001/up")
    return "\n".join(lines), ts


T0 = datetime(2026, 10, 20, 0, 0, 0)


# ─────────────────────── 解析 ───────────────────────
def test_parse_extracts_matching_lines_only():
    text, ts = log_lines(T0, [30, 30])
    text += "\n2026-10-20 00:02:00,1 [INFO] app.other: 无关日志\n"
    got = pec.parse_lines(text)
    assert got == ts                      # 只取匹配行，且升序


def test_parse_ignores_unmatched_pattern():
    text, _ = log_lines(T0, [30])
    assert pec.parse_lines(text, pattern="根本不存在的关键词") == []


def test_parse_empty():
    assert pec.parse_lines("") == []


# ─────────────────────── 间隔分类 ───────────────────────
def test_all_periodic():
    gaps = [30.0, 29.0, 31.0, 30.0]
    c = pec.classify_intervals(gaps)
    assert c["periodic"]["count"] == 4
    assert c["periodic"]["pct"] == 100.0


def test_burst_and_sporadic_are_separated():
    gaps = [1.0, 4.0, 3600.0, 30.0, 47.0]
    c = pec.classify_intervals(gaps)
    assert c["burst"]["count"] == 2
    assert c["sporadic"]["count"] == 1
    assert c["periodic"]["count"] == 1
    assert c["other"]["count"] == 1


def test_cadence_is_configurable():
    """周期可配：60s 节律的设备不能按 30s 判。"""
    gaps = [60.0] * 5
    assert pec.classify_intervals(gaps, cadence=30.0)["other"]["count"] == 5
    assert pec.classify_intervals(gaps, cadence=60.0)["periodic"]["count"] == 5


def test_no_intervals_no_division_by_zero():
    assert pec.classify_intervals([]) == {}


# ─────────────────────── 汇总 ───────────────────────
def test_summarize_reports_total_span_and_by_day():
    # 16 段间隔 => 17 条；第 11 段跳 86400s 跨到第二天
    text, _ = log_lines(T0, [30] * 10 + [86400] + [30] * 5)
    ts = pec.parse_lines(text)
    doc = pec.summarize(ts)
    assert doc["total"] == 17
    assert doc["first_at"] == "2026-10-20 00:00:00"
    assert doc["by_day"]["2026-10-20"] == 11
    assert doc["by_day"]["2026-10-21"] == 6      # 跨界那条 + 之后 5 条
    assert doc["intervals"] == 16
    assert doc["median_interval_s"] == 30.0
    assert doc["classes"]["sporadic"]["count"] == 1   # 那次跨天的大间隔


def test_summarize_states_caveat():
    """口径里必须写明这是"按节律推定"，不许让人误读成逐条分类。"""
    doc = pec.summarize(pec.parse_lines(log_lines(T0, [30])[0]))
    assert "推定" in doc["caveat"]


def test_summarize_empty_is_not_a_crash():
    doc = pec.summarize([])
    assert doc["total"] == 0
    assert doc["first_at"] is None


# ─────────────────────── CLI ───────────────────────
def test_cli_reads_log_file(tmp_path):
    text, _ = log_lines(T0, [30] * 9)
    p = tmp_path / "pe.txt"
    p.write_text(text, encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--log-file", str(p), "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["total"] == 10
    assert doc["classes"]["periodic"]["count"] == 9


def test_cli_reports_zero_without_matching_lines(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("nothing here\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--log-file", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert "没有匹配到任何条目" in r.stdout


def test_cli_json_includes_cadence_used(tmp_path):
    """JSON 输出里要带上判定用的周期与容差，否则数字无法复核。"""
    text, _ = log_lines(T0, [60] * 3)
    p = tmp_path / "pe60.txt"
    p.write_text(text, encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--log-file", str(p),
                        "--cadence", "60", "--json"],
                       capture_output=True, text=True)
    doc = json.loads(r.stdout)
    assert doc["cadence_s"] == 60.0
    assert doc["classes"]["periodic"]["count"] == 3
