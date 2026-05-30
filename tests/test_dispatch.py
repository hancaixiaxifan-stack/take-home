from datetime import UTC, datetime

import pytest

from src.main import ParsedInfo, assign_staff, require_deepseek_api_key, serialize_ticket


@pytest.fixture()
def staff_config():
    return {
        "default_staff_id": "s_000",
        "staff": [
            {"staff_id": "s_000", "name": "前台兜底"},
            {"staff_id": "s_007", "name": "李工"},
            {"staff_id": "s_008", "name": "陈工"},
        ],
        "buildings": [
            {"building": 6, "staff_ids": ["s_006"]},
        ],
        "dispatch_rules": [
            {"intent_type": "plumbing", "staff_ids": ["s_007", "s_008"]},
            {"intent_type": "other", "staff_ids": []},
        ],
    }


def test_parsed_info_truncates_summary():
    parsed = ParsedInfo(
        building=3,
        room="402",
        intent_type="plumbing",
        urgency="high",
        summary="x" * 80,
    )

    assert parsed.summary == "x" * 50


def test_assign_staff_skips_unknown_building_staff_and_uses_intent_rule(staff_config):
    parsed = ParsedInfo(
        building=6,
        room="101",
        intent_type="plumbing",
        urgency="normal",
        summary="6栋101漏水",
    )

    assigned = assign_staff(parsed, staff_config)

    assert assigned["staff_id"] in {"s_007", "s_008"}
    assert assigned["name"] in {"李工", "陈工"}
    assert assigned["match_rule"] == "intent_type"


def test_assign_staff_falls_back_when_no_rule_has_valid_staff(staff_config):
    parsed = ParsedInfo(
        building=None,
        room=None,
        intent_type="other",
        urgency="low",
        summary="无法识别的报修内容",
    )

    assigned = assign_staff(parsed, staff_config)

    assert assigned == {
        "staff_id": "s_000",
        "name": "前台兜底",
        "match_rule": "fallback",
    }


def test_serialize_ticket_removes_mongo_id_and_formats_created_at():
    doc = {
        "_id": "mongo-object-id",
        "ticket_id": "tk_abc123",
        "parsed": {"intent_type": "plumbing"},
        "created_at": datetime(2026, 5, 30, 8, 0, tzinfo=UTC),
    }

    serialized = serialize_ticket(doc)

    assert "_id" not in serialized
    assert serialized["created_at"] == "2026-05-30T08:00:00Z"


@pytest.mark.parametrize("value", ["", "   ", "sk-your-key-here", "sk-placeholder"])
def test_require_deepseek_api_key_rejects_missing_or_placeholder(value):
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        require_deepseek_api_key(value)


def test_require_deepseek_api_key_accepts_real_looking_key():
    assert require_deepseek_api_key("sk-real-value") == "sk-real-value"
