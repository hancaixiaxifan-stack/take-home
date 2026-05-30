import asyncio
import hashlib
import json
import os
import random
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from openai import AsyncOpenAI
from pydantic import BaseModel, field_validator
from redis.asyncio import Redis


IntentType = Literal[
    "plumbing",
    "electrical",
    "appliance",
    "cleaning",
    "security",
    "public_facility",
    "complaint",
    "other",
]
Urgency = Literal["low", "normal", "high", "urgent"]

SYSTEM_PROMPT = """你是物业报修文本解析器，只返回 JSON 对象，不要返回 Markdown。
返回字段名和类型必须与 ParsedInfo 完全一致：
{
  "building": int 或 null,
  "room": string 或 null,
  "intent_type": "plumbing" | "electrical" | "appliance" | "cleaning" | "security" | "public_facility" | "complaint" | "other",
  "urgency": "low" | "normal" | "high" | "urgent",
  "summary": string
}
intent_type 含义：
- plumbing: 水管、漏水、马桶、水龙头
- electrical: 电路、跳闸、灯具、插座
- appliance: 空调、热水器、家电
- cleaning: 保洁、垃圾、卫生
- security: 安保、门禁、可疑人员
- public_facility: 公共设施、电梯、路灯、绿化
- complaint: 投诉、纠纷
- other: 其他
无法理解的文本，例如乱码、表情、单字，返回 intent_type=other, urgency=low, summary="无法识别的报修内容"，不要拒绝。
不要臆造 staff 信息；派单不由你负责。"""

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
REDIS_URI = os.getenv("REDIS_URI", "redis://localhost:6379")
DB_NAME = os.getenv("DB_NAME", "desun")
STAFF_JSON_PATH = os.getenv("STAFF_JSON_PATH", "staff.json")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

mongo_client: AsyncIOMotorClient | None = None
redis_client: Redis | None = None
db: Any = None
staff_config: dict[str, Any] = {}
llm_client: AsyncOpenAI | None = None


class TicketRequest(BaseModel):
    user_id: str
    text: str


class ParsedInfo(BaseModel):
    building: Optional[int] = None
    room: Optional[str] = None
    intent_type: IntentType
    urgency: Urgency
    summary: str

    @field_validator("summary")
    @classmethod
    def truncate(cls, v: str) -> str:
        return v[:50]


class ParseError(Exception):
    pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    global mongo_client, redis_client, db, staff_config, llm_client

    staff_config = load_staff_config(STAFF_JSON_PATH)
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[DB_NAME]
    redis_client = Redis.from_url(REDIS_URI, decode_responses=True)
    api_key = require_deepseek_api_key(DEEPSEEK_API_KEY)
    llm_client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    await create_indexes()
    try:
        yield
    finally:
        if mongo_client is not None:
            mongo_client.close()
        if redis_client is not None:
            await redis_client.aclose()


app = FastAPI(title="DESUN Dispatch Service", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def create_indexes() -> None:
    await db.tickets.create_index([("parsed.building", 1), ("created_at", -1)])
    await db.tickets.create_index([("parsed.intent_type", 1), ("created_at", -1)])
    await db.tickets.create_index([("status", 1), ("created_at", -1)])
    await db.tickets.create_index([("created_at", -1)])
    await db.notifications.create_index([("ticket_id", 1)])


def load_staff_config(path: str) -> dict[str, Any]:
    staff_path = Path(path)
    if not staff_path.is_absolute():
        staff_path = Path.cwd() / staff_path
    return json.loads(staff_path.read_text(encoding="utf-8"))


def require_deepseek_api_key(value: str) -> str:
    api_key = value.strip()
    if not api_key or api_key in {"sk-your-key-here", "sk-placeholder"}:
        raise RuntimeError("DEEPSEEK_API_KEY must be set to a real DeepSeek API key")
    return api_key


def assign_staff(parsed: ParsedInfo, config: dict[str, Any]) -> dict[str, str]:
    staff_by_id = {item["staff_id"]: item for item in config.get("staff", [])}

    if parsed.building is not None:
        for rule in config.get("buildings", []):
            if rule.get("building") == parsed.building:
                assigned = choose_existing_staff(rule.get("staff_ids", []), staff_by_id)
                if assigned:
                    return staff_response(assigned, "building")
                break

    for rule in config.get("dispatch_rules", []):
        if rule.get("intent_type") == parsed.intent_type:
            assigned = choose_existing_staff(rule.get("staff_ids", []), staff_by_id)
            if assigned:
                return staff_response(assigned, "intent_type")
            break

    default_staff = staff_by_id[config["default_staff_id"]]
    return staff_response(default_staff, "fallback")


def choose_existing_staff(
    staff_ids: list[str], staff_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    candidates = [staff_by_id[staff_id] for staff_id in staff_ids if staff_id in staff_by_id]
    if not candidates:
        return None
    return random.choice(candidates)


def staff_response(staff: dict[str, Any], match_rule: str) -> dict[str, str]:
    return {
        "staff_id": staff["staff_id"],
        "name": staff["name"],
        "match_rule": match_rule,
    }


def serialize_ticket(doc: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(doc)
    item.pop("_id", None)
    item.pop("user_id", None)
    item.pop("text", None)
    for key in ("created_at", "sent_at"):
        if isinstance(item.get(key), datetime):
            item[key] = to_iso_z(item[key])
    return item


def to_iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


async def parse_ticket_text(text: str) -> ParsedInfo:
    try:
        assert llm_client is not None
        response = await llm_client.chat.completions.create(
            model="deepseek-chat",
            response_format={"type": "json_object"},
            timeout=15,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ParseError("LLM 返回空内容")
        raw = json.loads(content)
        return ParsedInfo(**raw)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(str(exc)) from exc


async def write_notification(
    ticket_id: str, staff_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    sent_at = datetime.now(UTC)
    await db.notifications.insert_one(
        {
            "ticket_id": ticket_id,
            "staff_id": staff_id,
            "payload": payload,
            "sent_at": sent_at,
            "via": "mock-bot",
        }
    )
    return {"sent": True, "via": "mock-bot"}


def make_ticket_id() -> str:
    return f"tk_{uuid.uuid4().hex[:8]}"


def make_idem_key(user_id: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"idem:tickets:{user_id}:{digest}"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tickets")
async def create_ticket(request: TicketRequest) -> JSONResponse:
    assert redis_client is not None
    idem_key = make_idem_key(request.user_id, request.text)

    occupied = await redis_client.set(idem_key, "PENDING", ex=300, nx=True)
    if not occupied:
        existing = await wait_for_idempotent_result(idem_key)
        if existing:
            return JSONResponse(serialize_ticket(existing), status_code=200)
        await redis_client.delete(idem_key)
        await redis_client.set(idem_key, "PENDING", ex=300, nx=True)

    ticket_id = make_ticket_id()
    created_at = datetime.now(UTC)
    try:
        parsed = await parse_ticket_text(request.text)
    except Exception as exc:
        await redis_client.delete(idem_key)
        error = str(exc) or "LLM 解析失败"
        await db.tickets.insert_one(
            {
                "ticket_id": ticket_id,
                "user_id": request.user_id,
                "text": request.text,
                "status": "parse_failed",
                "error": error,
                "created_at": created_at,
            }
        )
        return JSONResponse(
            {"ticket_id": ticket_id, "status": "parse_failed", "error": error},
            status_code=422,
        )

    assigned_to = assign_staff(parsed, staff_config)
    notification = await write_notification(
        ticket_id,
        assigned_to["staff_id"],
        {
            "ticket_id": ticket_id,
            "user_id": request.user_id,
            "text": request.text,
            "parsed": parsed.model_dump(),
            "assigned_to": assigned_to,
        },
    )
    doc = {
        "ticket_id": ticket_id,
        "user_id": request.user_id,
        "text": request.text,
        "parsed": parsed.model_dump(),
        "assigned_to": assigned_to,
        "notification": notification,
        "status": "open",
        "created_at": created_at,
    }
    await db.tickets.insert_one(doc)
    await redis_client.set(idem_key, ticket_id, ex=300)
    return JSONResponse(serialize_ticket(doc), status_code=201)


async def wait_for_idempotent_result(idem_key: str) -> dict[str, Any] | None:
    assert redis_client is not None
    for _ in range(10):
        value = await redis_client.get(idem_key)
        if value and value != "PENDING":
            doc = await db.tickets.find_one({"ticket_id": value})
            if doc:
                return doc
        await asyncio.sleep(0.15)
    return None


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str) -> dict[str, Any]:
    doc = await db.tickets.find_one({"ticket_id": ticket_id})
    if not doc:
        raise HTTPException(status_code=404, detail="ticket not found")
    return serialize_ticket(doc)


@app.get("/tickets")
async def list_tickets(
    building: int | None = Query(default=None),
    intent_type: IntentType | None = Query(default=None),
    status: Literal["open", "parse_failed"] | None = Query(default=None),
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if building is not None:
        query["parsed.building"] = building
    if intent_type is not None:
        query["parsed.intent_type"] = intent_type
    if status is not None:
        query["status"] = status

    cursor = db.tickets.find(query).sort("created_at", -1).limit(50)
    items = [serialize_ticket(doc) async for doc in cursor]
    total = await db.tickets.count_documents(query)
    return {"items": items, "total": total}
