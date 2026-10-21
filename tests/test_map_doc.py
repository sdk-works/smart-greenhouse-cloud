"""地图文档模型单元测试：旧数据迁移 + 提交时保留绑定（前端会剥离 binding）。"""
from app.core.map_doc import apply_document, normalize_doc, to_public


def test_normalize_empty_returns_safe_defaults():
    d = normalize_doc(None)
    assert d["points"] == []
    assert d["layout"]["width"] == 10.0 and d["layout"]["height"] == 80.0
    assert d["defaults"]["sample_seconds"] == 20
    pub = to_public(d)
    assert set(["revision", "layout", "defaults", "next_name", "points"]).issubset(pub)


def test_normalize_legacy_waypoints_is_migrated():
    d = normalize_doc({"width": 100, "height": 60, "waypoints": [
        {"id": 1, "label": "1号点", "x": 10, "y": 20, "description": "北侧"},
    ]})
    assert d["layout"]["width"] == 100 and d["layout"]["height"] == 60
    assert d["points"][0]["point_name"] == "1号点"
    assert d["points"][0]["x"] == 10 and d["points"][0]["y"] == 20
    assert d["points"][0]["binding"] is None


def test_apply_document_preserves_existing_binding():
    cur = normalize_doc({
        "revision": 2,
        "points": [{
            "id": "p1", "point_name": "A1", "x": 1, "y": 2,
            "binding": {"robot_id": "ROB001", "nav_point_code": "7", "mapping_id": "m-1"},
        }],
    })
    incoming = {
        "revision": 2,
        "points": [{"id": "p1", "point_name": "A1-改名", "x": 3, "y": 4}],  # 无 binding
    }
    out = apply_document(cur, incoming)
    assert out["points"][0]["point_name"] == "A1-改名"
    assert out["points"][0]["binding"]["mapping_id"] == "m-1"


def test_apply_document_drops_removed_points_and_adds_new():
    cur = normalize_doc({"revision": 1, "points": [
        {"id": "p1", "point_name": "A1"}, {"id": "p2", "point_name": "A2"},
    ]})
    incoming = {"revision": 1, "points": [{"id": "p2", "point_name": "A2"}, {"id": "p3", "point_name": "A3"}]}
    out = apply_document(cur, incoming)
    ids = [p["id"] for p in out["points"]]
    assert ids == ["p2", "p3"]
    assert next(p for p in out["points"] if p["id"] == "p3")["binding"] is None
