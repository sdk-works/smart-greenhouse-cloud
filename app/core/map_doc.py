"""
map_doc.py — 大棚地图文档的唯一读写入口

文档形状与前端 pages/pointMapUtils.js / PointConfigPage.js 严格对齐：
    {
      "revision": int,
      "layout":  {template, width, height, calibrated, aisle_side, aisle_width,
                  bays, length_axis, planting_direction, collection_lane_count,
                  collection_direction},
      "defaults": {sample_seconds, collect_soil, collect_environment, take_photo, enabled},
      "next_name": str,
      "points": [ {id, point_name, zone, x, y, note, sample_seconds, collect_soil,
                   collect_environment, take_photo, enabled,
                   binding: null | {robot_id, nav_point_code, mapping_id, point_name}} ]
    }

存储位置仍是 greenhouses.map_data(JSON)，不新增数据库列。
同时兼容旧形状 {width, height, waypoints:[{id,x,y,label,...}]}，读取时自动迁移。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

MAX_POINTS = 500

DEFAULT_LAYOUT: Dict[str, Any] = {
    "template": "solar",
    "width": 10.0,
    "height": 80.0,
    "calibrated": False,
    "aisle_side": "north",
    "aisle_width": 1.5,
    "bays": 4,
    "length_axis": "ew",
    "planting_direction": "ns",
    "collection_lane_count": 0,
    "collection_direction": "ns",
}

DEFAULT_DEFAULTS: Dict[str, Any] = {
    "sample_seconds": 20,
    "collect_soil": True,
    "collect_environment": True,
    "take_photo": True,
    "enabled": True,
}

POINT_FIELDS = [
    "id", "point_name", "zone", "x", "y", "note", "sample_seconds",
    "collect_soil", "collect_environment", "take_photo", "enabled",
]

_FLAGS = ("collect_soil", "collect_environment", "take_photo", "enabled")


def new_point_id() -> str:
    return str(uuid.uuid4())


def _clean(d: Optional[Dict[str, Any]], base: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    if isinstance(d, dict):
        for k, v in d.items():
            if v is not None:
                out[k] = v
    return out


def _normalize_binding(b: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(b, dict):
        return None
    code = b.get("nav_point_code")
    if code is None or str(code).strip() == "":
        return None
    return {
        "robot_id": b.get("robot_id"),
        "nav_point_code": str(code),
        "mapping_id": b.get("mapping_id"),
        "point_name": b.get("point_name"),
    }


def _normalize_point(p: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {k: p.get(k) for k in POINT_FIELDS}
    if not out.get("id"):
        out["id"] = new_point_id()
    out["id"] = str(out["id"])
    if not out.get("point_name"):
        out["point_name"] = "未命名"
    if out.get("point_name") is not None:
        out["point_name"] = str(out["point_name"])
    for k in ("sample_seconds",) + _FLAGS:
        if out.get(k) is None:
            out[k] = defaults.get(k)
    out["binding"] = _normalize_binding(p.get("binding"))
    return out


def _legacy_point(w: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(w.get("id") if w.get("id") is not None else new_point_id()),
        "point_name": w.get("label") or w.get("code") or w.get("name") or "未命名",
        "zone": w.get("zone") or "",
        "x": w.get("x"),
        "y": w.get("y"),
        "note": w.get("description") or w.get("note") or "",
        "sample_seconds": defaults.get("sample_seconds"),
        "collect_soil": defaults.get("collect_soil"),
        "collect_environment": defaults.get("collect_environment"),
        "take_photo": defaults.get("take_photo"),
        "enabled": defaults.get("enabled"),
        "binding": None,
    }


def normalize_doc(map_data: Any) -> Dict[str, Any]:
    """把任意历史形状的 map_data 归一化为标准文档。"""
    md = dict(map_data) if isinstance(map_data, dict) else {}

    layout = md.get("layout")
    if not isinstance(layout, dict):
        layout = dict(DEFAULT_LAYOUT)
        if md.get("width") is not None:
            layout["width"] = md["width"]
        if md.get("height") is not None:
            layout["height"] = md["height"]
    layout = _clean(layout, DEFAULT_LAYOUT)

    defaults = _clean(md.get("defaults"), DEFAULT_DEFAULTS)

    raw_points = md.get("points")
    if isinstance(raw_points, list):
        points = [_normalize_point(p, defaults) for p in raw_points if isinstance(p, dict)]
    else:
        raw_waypoints = md.get("waypoints")
        points = [_legacy_point(w, defaults) for w in raw_waypoints if isinstance(w, dict)] \
            if isinstance(raw_waypoints, list) else []

    return {
        "revision": int(md.get("revision") or 0),
        "layout": layout,
        "defaults": defaults,
        "next_name": md.get("next_name") or "A1",
        "points": points,
        "structure": md.get("structure") or "solar",
        "covering": md.get("covering") or "film",
    }


def to_public(doc: Dict[str, Any]) -> Dict[str, Any]:
    """返回给前端的文档视图（与 editableDocument/doc 结构一致）。"""
    return {
        "revision": int(doc.get("revision") or 0),
        "layout": doc.get("layout") or dict(DEFAULT_LAYOUT),
        "defaults": doc.get("defaults") or dict(DEFAULT_DEFAULTS),
        "next_name": doc.get("next_name") or "A1",
        "points": doc.get("points") or [],
    }


def apply_document(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """
    把前端提交的文档（editableDocument，不含 binding）合并到现有文档。
    - 按 point.id 保留原 binding（前端有意剥离 binding）
    - 删除提交里已不存在的点
    - 新点 binding=None
    """
    cur = normalize_doc(existing)
    by_id = {pt["id"]: pt for pt in cur["points"]}

    layout = incoming.get("layout")
    defaults = _clean(incoming.get("defaults"), cur["defaults"])

    new_points: List[Dict[str, Any]] = []
    for p in incoming.get("points") or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or new_point_id())
        base = by_id.get(pid)
        pt: Dict[str, Any] = {k: p.get(k) for k in POINT_FIELDS}
        pt["id"] = pid
        if not pt.get("point_name"):
            pt["point_name"] = (base or {}).get("point_name") or "未命名"
        for k in ("sample_seconds",) + _FLAGS:
            if pt.get(k) is None:
                pt[k] = defaults.get(k)
        pt["binding"] = (base or {}).get("binding")
        new_points.append(pt)

    return {
        "revision": int(cur.get("revision") or 0),
        "layout": _clean(layout, cur["layout"]),
        "defaults": defaults,
        "next_name": incoming.get("next_name") or cur.get("next_name") or "A1",
        "points": new_points,
        "structure": incoming.get("structure") or cur.get("structure") or "solar",
        "covering": incoming.get("covering") or cur.get("covering") or "film",
    }


def bump(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc)
    out["revision"] = int(doc.get("revision") or 0) + 1
    return out
