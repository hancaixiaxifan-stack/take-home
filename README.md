# 微型物业派单系统

接收业主自由文本报修，LLM 解析关键字段，按规则派单，Mock 通知，MongoDB 持久化。

## 前置条件

- **Docker**：需安装 Docker Desktop（或 Docker Engine + Docker Compose V2）
- **DeepSeek API Key**：前往 [platform.deepseek.com](https://platform.deepseek.com) 注册并创建 API Key（本项目全程调用成本 < $0.01）

## 快速启动（Docker）

```bash
# 1. 克隆仓库
git clone https://github.com/hancaixiaxifan-stack/take-home.git
cd take-home

# 2. 创建环境变量文件
cp .env.example .env

# 3. 编辑 .env，将 sk-your-key-here 替换为真实的 DeepSeek API Key
#    Windows:  notepad .env
#    macOS:    open -e .env
#    Linux:    nano .env

# 4. 构建并启动（首次会拉取镜像，约 1-2 分钟）
docker compose up -d --build

# 5. 等待服务就绪（app 容器 healthcheck 通过即可）
docker compose ps
# 看到 app 容器状态为 healthy 即表示就绪
```

服务启动后监听 `http://localhost:8080`。

## 验证服务

```bash
# 健康检查
curl http://localhost:8080/health

# 创建一条工单
curl -X POST http://localhost:8080/tickets \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u_001","text":"3栋402卫生间漏水，急"}'
```

POST 返回示例：

```json
{
  "ticket_id": "tk_abc123",
  "parsed": {
    "building": 3,
    "room": "402",
    "intent_type": "plumbing",
    "urgency": "high",
    "summary": "3栋402卫生间漏水，急"
  },
  "assigned_to": {
    "staff_id": "s_003",
    "name": "...",
    "match_rule": "building"
  },
  "notification": {
    "sent": true,
    "via": "mock-bot"
  },
  "status": "open",
  "created_at": "2026-05-30T12:00:00Z"
}
```

## 公开测试脚本

仓库提供了 3 个集成测试脚本（需要服务已启动）：

```bash
# 基本流程：创建工单 + 查询
bash public-tests/test_basic.sh

# 幂等性：重复提交返回相同 ticket_id
bash public-tests/test_idempotency.sh

# LLM 兜底：无意义文本不导致 500
bash public-tests/test_llm_fallback.sh
```

脚本默认访问 `http://localhost:8080`，可通过环境变量覆盖：`BASE_URL=http://其他地址 bash public-tests/test_basic.sh`。

> **Windows 用户注意**：公开测试脚本使用 bash + curl + jq，在 Windows Git Bash 下中文字符编码可能返回 400 错误。建议使用 WSL（Windows Subsystem for Linux）或 PowerShell 运行测试，或直接通过 Docker 容器内执行。

## API 文档

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/tickets` | 创建工单（body: `{"user_id":"...","text":"..."}`） |
| GET | `/tickets/{ticket_id}` | 查询单条工单 |
| GET | `/tickets?building=&intent_type=&status=` | 列表查询（三个参数任意组合） |

## 本地开发（不用 Docker）

本地运行需要先启动 MongoDB 和 Redis，可以用 Docker 单独启动这两个服务：

```bash
# 只启动数据库服务
docker compose up -d mongo redis

# 安装 Python 依赖
pip install -r requirements-dev.txt

# 设置环境变量（Windows PowerShell 用 $env:DEEPSEEK_API_KEY = "sk-..."）
export DEEPSEEK_API_KEY=sk-your-real-key
export MONGO_URI=mongodb://localhost:27017
export REDIS_URI=redis://localhost:6379
export DB_NAME=desun
export STAFF_JSON_PATH=./staff.json

# 启动服务
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

运行单元测试（纯单元测试不需要数据库，集成测试需要 MongoDB/Redis）：

```bash
pip install -r requirements-dev.txt
pytest -v
```

## 设计要点

- FastAPI 所有业务逻辑集中在 `src/main.py`，使用 `motor` 访问 MongoDB、`redis.asyncio` 实现 5 分钟幂等 key。
- LLM 路径只使用 DeepSeek：OpenAI SDK，`base_url=https://api.deepseek.com`，`model=deepseek-chat`，并开启 `response_format={"type":"json_object"}`；返回值必须经过 `ParsedInfo` Pydantic schema 校验。
- 派单严格按楼栋、类型、兜底顺序。楼栋规则中的 `staff_id` 不存在时不会分配给无效人员，会继续尝试类型规则。
- Mock 通知写入 MongoDB `notifications` 集合，API 返回 `{ "sent": true, "via": "mock-bot" }`。
- CORS 允许所有来源，支持直接打开 `frontend/index.html` 访问本地服务。

## 索引

启动时自动创建以下 MongoDB 索引：

| 集合 | 索引 | 用途 |
|------|------|------|
| tickets | `parsed.building` + `created_at` | 按楼栋筛选 |
| tickets | `parsed.intent_type` + `created_at` | 按类型筛选 |
| tickets | `status` + `created_at` | 按状态筛选 |
| tickets | `created_at` | 时间排序 |
| notifications | `ticket_id` | 按工单查通知 |

## LLM 模型选型

选用 DeepSeek（`deepseek-chat`），理由：

1. **成本**：按 token 计费，单价低，本项目全程调用成本 < $0.01，符合题目"总成本应 < $1"的要求。
2. **中文能力**：报修文本为中文口语（如"3栋402卫生间漏水严重，急死了"），DeepSeek 作为国内模型，对中文口语的理解和字段提取优于同等价位的海外模型。
3. **结构化输出**：支持 `response_format={"type": "json_object"}`，配合 Pydantic schema 校验，能稳定返回符合要求的 JSON，无需额外正则提取。
4. **兼容性**：兼容 OpenAI SDK（`base_url=https://api.deepseek.com`），迁移成本低，无需引入额外依赖。

## Trade-offs

**1. 所有逻辑放在单文件 `src/main.py`（353 行）**

AI 建议拆成 `routers/`、`models/`、`services/` 多文件架构。我选择保留单文件：take-home 项目评审人需要快速看懂全貌，单文件一目了然，拆多文件反而增加阅读和跳转成本。353 行尚在可控范围内，如果业务继续膨胀再拆不迟。

**3. 幂等去重用轮询而非 pub/sub**

当前实现中，当两个请求同时命中同一幂等 key 时，第二个请求通过轮询 Redis（10 次 × 150ms）等待第一个请求写入结果。更优雅的方案是 Redis pub/sub 或 asyncio.Event，但会显著增加复杂度。对本项目的并发规模，轮询方案足够。

**4. 派单随机选择而非负载均衡**

当前 `random.choice` 从匹配员工中随机选取，不考虑每人手头有多少未完成工单。理想方案是查询 MongoDB 中每个候选员工的 active ticket 数量，派给最闲的人。但这需要每次派单时额外查询数据库，且在并发场景下可能同时派给同一人（需要分布式锁）。本项目规模下随机选择可接受，生产环境应引入负载均衡机制。

**5. LLM 解析失败不自动重试**

LLM 调用失败时直接返回 422 + `parse_failed`，由用户决定是否重试，而不是自动重试 2-3 次。理由：如果 LLM 服务持续不可用，自动重试会浪费时间和资源，用户等待体验更差。快速失败 + 让用户主动重试是更可控的策略。

## 停止服务

```bash
docker compose down
```

如需清除数据（MongoDB/Redis 数据卷）：`docker compose down -v`。
