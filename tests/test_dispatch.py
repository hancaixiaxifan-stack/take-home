from datetime import UTC, datetime

import httpx
import pytest

import src.main as dispatch
from src.main import ParseError, ParsedInfo, assign_staff, require_deepseek_api_key, serialize_ticket


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


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(doc.copy())

    async def find_one(self, query):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc.copy()
        return None


class FakeDB:
    def __init__(self):
        self.tickets = FakeCollection()
        self.notifications = FakeCollection()


@pytest.fixture()
def fake_runtime(monkeypatch, staff_config):
    fake_db = FakeDB()
    fake_redis = FakeRedis()
    monkeypatch.setattr(dispatch, "db", fake_db)
    monkeypatch.setattr(dispatch, "redis_client", fake_redis)
    monkeypatch.setattr(dispatch, "staff_config", staff_config)
    monkeypatch.setattr(dispatch, "make_ticket_id", lambda: f"tk_{len(fake_db.tickets.docs) + 1:06d}")
    return fake_db, fake_redis


@pytest.mark.asyncio
async def test_post_tickets_creates_ticket_assigns_staff_and_writes_notification(
    monkeypatch, fake_runtime
):
    fake_db, _ = fake_runtime

    async def parse_stub(text):
        return ParsedInfo(
            building=6,
            room="101",
            intent_type="plumbing",
            urgency="high",
            summary="6栋101漏水",
        )

    monkeypatch.setattr(dispatch, "parse_ticket_text", parse_stub)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dispatch.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/tickets",
            json={"user_id": "u_001", "text": "6栋101漏水，急"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["ticket_id"] == "tk_000001"
    assert body["parsed"]["intent_type"] == "plumbing"
    assert body["assigned_to"]["match_rule"] == "intent_type"
    assert body["notification"] == {"sent": True, "via": "mock-bot"}
    assert body["status"] == "open"
    assert len(fake_db.tickets.docs) == 1
    assert len(fake_db.notifications.docs) == 1


@pytest.mark.asyncio
async def test_post_tickets_returns_existing_ticket_on_idempotency_hit(
    monkeypatch, fake_runtime
):
    fake_db, _ = fake_runtime
    parse_calls = 0

    async def parse_stub(text):
        nonlocal parse_calls
        parse_calls += 1
        return ParsedInfo(
            building=None,
            room=None,
            intent_type="plumbing",
            urgency="normal",
            summary="水管漏了",
        )

    monkeypatch.setattr(dispatch, "parse_ticket_text", parse_stub)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dispatch.app),
        base_url="http://test",
    ) as client:
        first = await client.post("/tickets", json={"user_id": "u_001", "text": "水管漏了"})
        second = await client.post("/tickets", json={"user_id": "u_001", "text": "水管漏了"})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["ticket_id"] == first.json()["ticket_id"]
    assert parse_calls == 1
    assert len(fake_db.tickets.docs) == 1
    assert len(fake_db.notifications.docs) == 1


@pytest.mark.asyncio
async def test_post_tickets_returns_422_and_releases_idempotency_key_on_llm_failure(
    monkeypatch, fake_runtime
):
    fake_db, fake_redis = fake_runtime

    async def parse_stub(text):
        raise ParseError("LLM 返回非法 JSON")

    monkeypatch.setattr(dispatch, "parse_ticket_text", parse_stub)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dispatch.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/tickets",
            json={"user_id": "u_001", "text": "bad llm"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "parse_failed"
    assert body["error"] == "LLM 返回非法 JSON"
    assert len(fake_db.tickets.docs) == 1
    assert fake_db.tickets.docs[0]["status"] == "parse_failed"
    assert len(fake_db.notifications.docs) == 0
    assert fake_redis.values == {}
