from app.mqtt.mqtt_service import _normalize_realtime_data


def test_adds_db_aliases_and_keeps_raw():
    d = _normalize_realtime_data({"temperature": 25.8, "co2": 651, "light": 2362, "ph": 6.5})
    assert d["co2_level"] == 651
    assert d["light_intensity"] == 2362
    assert d["ph_value"] == 6.5
    assert d["co2"] == 651 and d["light"] == 2362


def test_does_not_override_existing_values():
    d = _normalize_realtime_data({"co2_level": 10, "co2": 99})
    assert d["co2_level"] == 10


def test_handles_non_dict():
    assert _normalize_realtime_data(None) == {}


def test_no_alias_when_raw_missing():
    d = _normalize_realtime_data({"temperature": 25.8})
    assert "co2_level" not in d and "light_intensity" not in d
