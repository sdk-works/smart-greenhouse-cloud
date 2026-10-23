"""上行报文解析错误分类（**候选，未接线**）。

背景：生产每 30 秒收到一条 ASCII `www.usr.cn`（USR DTU 的注册包/心跳包被透传进
`agri/device/JY001/up`），服务端 `json.loads` 失败后只打一条 WARNING 就丢，
既不落 `raw_messages`，也不分类。于是"解析错误"只有一个总数，无法解释。

本模块做的事：
  1. **在解析之前**先按字节分类，四类分开计数；
  2. 给出可落库的 `RawMessage`（`parse_status='rejected'`）+ 分类码；
  3. 另给一张聚合表 `parse_error_stats` 的 upsert —— 按 (设备, 分类, 报文签名) 计数，
     避免每 30 秒一条垃圾把 `raw_messages` 撑大还能拿到"重复了多少次"。

四类（与任务书一致）：
  non_json              不是合法 JSON（含"能解析但不是对象"）
  duplicate_registration 命中已知厂家注册包/心跳包签名（www.usr.cn 之类），且是重复出现
  device_mismatch       报文自称的 device_id 与 topic 里的设备不一致
  missing_field         JSON 合法但缺必需字段（msg_id / type）

**未接线**：`app/mqtt/mqtt_service.py::_dispatch_payload` 现在仍是"失败即丢"。
接线方式见 `docs/parse-error-classification-20261023.md` §4（3 行 diff，需人批准）。
本模块可以独立运行：`python -m app.core.parse_error_classifier 'www.usr.cn'`
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone

# ── 分类码 ────────────────────────────────────────────────────
OK = "ok"
NON_JSON = "non_json"
DUPLICATE_REGISTRATION = "duplicate_registration"
DEVICE_MISMATCH = "device_mismatch"
MISSING_FIELD = "missing_field"

ALL_KINDS = (NON_JSON, DUPLICATE_REGISTRATION, DEVICE_MISMATCH, MISSING_FIELD)

# 已知厂家注册包 / 心跳包签名（去空白后比较，小写）
KNOWN_REGISTRATION_SIGNATURES = {
    "www.usr.cn",
    "www.usr.cn\r\n",
    "usr.cn",
}

# device 通道遥测的必需字段（契约 docs/telemetry-v1.md）
REQUIRED_DEVICE_FIELDS = ("msg_id",)


def signature(payload: bytes, limit: int = 32) -> str:
    """报文指纹：可打印就原文（截断），否则取 sha1 前 12 位。

    目的是把"同一台设备反复发同一条垃圾"聚成一行，而不是每 30 秒一行。
    """
    if payload is None:
        return ""
    head = payload[:limit]
    try:
        text = head.decode("utf-8")
        if text.isprintable():
            return text
    except Exception:
        pass
    return "sha1:" + hashlib.sha1(payload).hexdigest()[:12]


def _topic_device(topic: str) -> str:
    """`agri/device/JY001/up` -> `JY001`；取不到返回空串。"""
    parts = (topic or "").split("/")
    return parts[2] if len(parts) >= 3 else ""


def classify(topic: str, payload: bytes, *, seen_before: bool = False):
    """返回 (kind, detail, parsed)。

    parsed 只在 kind == OK 时是 dict，其它情况为 None。
    seen_before 由调用方用 parse_error_stats 的计数给出，用来区分
    "第一次见到这个注册包" 与 "已知签名且重复出现"。
    """
    if payload is None:
        return NON_JSON, "payload 为空", None

    raw = payload.decode("utf-8", errors="replace").strip()

    # ── 1) 非 JSON ────────────────────────────────────────────
    try:
        parsed = json.loads(raw)
    except Exception as e:
        if raw.strip().lower() in KNOWN_REGISTRATION_SIGNATURES:
            detail = (f"厂家注册包/心跳包签名 {raw!r}"
                      f"（{'重复出现' if seen_before else '首次出现'}）")
            return DUPLICATE_REGISTRATION, detail, None
        return NON_JSON, f"JSON 解析失败: {type(e).__name__}: {e}", None

    # 能解析但不是对象，同样当成"不是一份报文"
    if not isinstance(parsed, dict):
        return NON_JSON, f"JSON 顶层不是对象，实际为 {type(parsed).__name__}", None

    # ── 2) 非本设备 ───────────────────────────────────────────
    topic_dev = _topic_device(topic)
    claimed = parsed.get("device_id")
    if topic_dev and claimed and str(claimed) != topic_dev:
        return DEVICE_MISMATCH, f"topic 设备={topic_dev}，报文自称 device_id={claimed}", parsed

    # ── 3) 字段缺失 ───────────────────────────────────────────
    missing = [f for f in REQUIRED_DEVICE_FIELDS if f not in parsed]
    if missing:
        return MISSING_FIELD, f"缺少必需字段: {','.join(missing)}", parsed

    return OK, "", parsed


def build_rejected_raw_message(topic: str, payload: bytes, kind: str, detail: str,
                               *, source: str = "mqtt", parser_version: str = "v1-parse-diag"):
    """构造一条"解析被拒"的 raw_messages 行（**不 commit**，交给调用方）。

    注意：`payload` 列是 JSON 类型，非 JSON 报文存不进去，所以留 NULL，
    原始字节长度存 `payload_bytes`，原文落在 `error` 里（截断 200 字符）。
    """
    from app.models.models import RawMessage

    return RawMessage(
        msg_id=None,
        source=source,
        device_id=_topic_device(topic) or None,
        received_at=datetime.now(timezone.utc),
        payload=None,
        payload_bytes=len(payload or b""),
        parse_status="rejected",
        parser_version=parser_version,
        error=f"[{kind}] {detail} | raw={signature(payload, 200)!r}",
    )


UPSERT_STAT_SQL = """
INSERT INTO parse_error_stats (device_id, kind, signature, cnt, first_seen, last_seen, last_detail)
VALUES (:device_id, :kind, :signature, 1, now(), now(), :detail)
ON CONFLICT (device_id, kind, signature) DO UPDATE
   SET cnt = parse_error_stats.cnt + 1,
       last_seen = now(),
       last_detail = EXCLUDED.last_detail
RETURNING cnt
"""


async def record_parse_error(session_factory, topic: str, payload: bytes, kind: str,
                             detail: str, *, persist_raw: bool = True):
    """一次调用完成：写 raw_messages（可选）+ upsert 聚合计数。

    返回聚合后的 cnt（这条签名累计出现次数）。任何异常都吞掉并记日志 ——
    **观测链路不能影响主链路**（解析失败本来就是要丢的报文，不能因为它把收包卡住）。
    """
    import logging

    logger = logging.getLogger(__name__)
    from sqlalchemy import text as sa_text

    dev = _topic_device(topic) or "<unknown>"
    sig = signature(payload)
    cnt = None
    try:
        async with session_factory() as db:
            cnt = (await db.execute(sa_text(UPSERT_STAT_SQL), {
                "device_id": dev, "kind": kind, "signature": sig, "detail": detail[:500],
            })).scalar()
            if persist_raw:
                db.add(build_rejected_raw_message(topic, payload, kind, detail))
            await db.commit()
    except Exception as e:      # pragma: no cover - 观测链路不允许抛穿
        logger.warning(f"[PARSE-DIAG] 记录解析错误失败（不影响主链路）: {e}")
    return cnt


def _main(argv):
    """独立运行：给一段报文原文，打印分类结果。"""
    if not argv:
        print("用法: python -m app.core.parse_error_classifier '<payload>' [topic]")
        return 2
    payload = argv[0].encode("utf-8")
    topic = argv[1] if len(argv) > 1 else "agri/device/JY001/up"
    kind, detail, parsed = classify(topic, payload)
    print(f"topic   : {topic}")
    print(f"payload : {argv[0]!r}")
    print(f"kind    : {kind}")
    print(f"detail  : {detail}")
    print(f"parsed  : {parsed}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
