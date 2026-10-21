"""AI 建议生成器（M4）——**纯函数模块，刻意不引入任何 IO**。

受控约束（明早验收请重点看这一条）：
  * 本模块**只 import 标准库**，不 import sqlalchemy / mqtt / redis。
    也就是说 AI 侧**拿不到数据库连接，也拿不到 MQTT 客户端**。
  * 入参是后端接口层给的"只读快照"（snapshot），AI 不能自己去查任何东西。
  * 产出只有"建议"，且禁止产出控制动作（control 类建议会被直接拒绝）。

数据由后端接口层按公司范围取好后传入，AI 只做判断。
"""
from typing import Any, Dict, List

# 允许的产出类型；control 类刻意不在其中
ALLOWED_ADVICE_KINDS = ("observation", "suggestion", "risk", "inspection_request")

FORBIDDEN_KINDS = ("control", "command", "actuate", "mqtt")


def _f(v) -> Any:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def generate_advice(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """根据快照生成建议。返回 {advice, basis, limits}。"""
    metrics = snapshot.get("metrics") or {}
    thresholds = snapshot.get("thresholds") or {}
    advice: List[Dict[str, Any]] = []

    for code, value in sorted(metrics.items()):
        v = _f(value)
        rule = thresholds.get(code)
        if v is None or not isinstance(rule, dict):
            continue
        lo, hi = _f(rule.get("min")), _f(rule.get("max"))
        if lo is not None and v < lo:
            advice.append({
                "kind": "risk", "metric": code, "value": v, "unit": rule.get("unit"),
                "message": f"{code} 偏低（{v}{rule.get('unit') or ''} < {lo}）",
                "severity": "warning",
            })
        elif hi is not None and v > hi:
            advice.append({
                "kind": "risk", "metric": code, "value": v, "unit": rule.get("unit"),
                "message": f"{code} 偏高（{v}{rule.get('unit') or ''} > {hi}）",
                "severity": "warning",
            })

    if not advice:
        advice.append({
            "kind": "observation", "metric": None, "value": None, "unit": None,
            "message": "各项指标均在合理区间内，无需处置", "severity": "info",
        })
    else:
        advice.append({
            "kind": "inspection_request", "metric": None, "value": None, "unit": None,
            "message": "建议人工复核信号来源（勿直接下发控制指令）",
            "severity": "info",
        })

    return {
        "advice": advice,
        "basis": {"metrics": metrics, "thresholds": thresholds,
                  "greenhouse_id": snapshot.get("greenhouse_id"),
                  "as_of": snapshot.get("as_of")},
        "limits": {
            "db_access": False,
            "mqtt_control": False,
            "read_only_snapshot": True,
            "note": "AI 只产出建议，不直连数据库、不下发任何控制指令",
        },
    }


def assert_no_control_action(advice: List[Dict[str, Any]]) -> None:
    """最后一道闸：任何建议都不许是控制动作。"""
    for item in advice:
        kind = str(item.get("kind") or "").strip().lower()
        if kind in FORBIDDEN_KINDS:
            raise ValueError(f"AI 不得产出控制类建议：{kind}")
        if kind not in ALLOWED_ADVICE_KINDS:
            raise ValueError(f"未知建议类型：{kind}")
