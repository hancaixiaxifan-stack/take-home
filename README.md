# 实现说明

## 启动

```bash
docker compose up -d --build
```

服务默认监听 `http://localhost:8080`。启动前必须复制 `.env.example` 为 `.env`，并把 `DEEPSEEK_API_KEY` 设置为真实 DeepSeek API key；未设置或仍为占位符时，应用会在启动阶段失败，避免绕过真实 LLM 链路。

## API 示例

```bash
curl -X POST http://localhost:8080/tickets \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u_001","text":"3栋402卫生间漏水，急"}'

curl http://localhost:8080/tickets/tk_xxxxxx
curl "http://localhost:8080/tickets?building=3&intent_type=plumbing&status=open"
curl http://localhost:8080/health
```

本地运行单元测试时安装开发依赖：

```bash
pip install -r requirements-dev.txt
pytest
```

## 设计要点

- FastAPI 所有业务逻辑集中在 `src/main.py`，使用 `motor` 访问 MongoDB、`redis.asyncio` 实现 5 分钟幂等 key。
- LLM 路径只使用 DeepSeek：OpenAI SDK，`base_url=https://api.deepseek.com`，`model=deepseek-chat`，并开启 `response_format={"type":"json_object"}`；返回值必须经过 `ParsedInfo` Pydantic schema 校验。
- 派单严格按楼栋、类型、兜底顺序。楼栋规则中的 `staff_id` 不存在时不会分配给无效人员，会继续尝试类型规则。
- Mock 通知写入 MongoDB `notifications` 集合，API 返回 `{ "sent": true, "via": "mock-bot" }`。
- CORS 允许所有来源，支持直接打开 `frontend/index.html` 访问本地服务。

## 索引

启动时创建以下索引：`tickets(parsed.building, created_at)`、`tickets(parsed.intent_type, created_at)`、`tickets(status, created_at)`、`tickets(created_at)`、`notifications(ticket_id)`。列表查询实际使用 `parsed.building` 和 `parsed.intent_type` 路径。

## LLM 模型选型

选用 DeepSeek（`deepseek-chat`），理由：

1. **成本**：按 token 计费，单价低，本项目全程调用成本 < $0.01，符合题目"总成本应 < $1"的要求。
2. **中文能力**：报修文本为中文口语（如"3栋402卫生间漏水严重，急死了"），DeepSeek 作为国内模型，对中文口语的理解和字段提取优于同等价位的海外模型。
3. **结构化输出**：支持 `response_format={"type": "json_object"}`，配合 Pydantic schema 校验，能稳定返回符合要求的 JSON，无需额外正则提取。
4. **兼容性**：兼容 OpenAI SDK（`base_url=https://api.deepseek.com`），迁移成本低，无需引入额外依赖。

