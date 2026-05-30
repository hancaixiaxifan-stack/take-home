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

## 设计要点

- FastAPI 所有业务逻辑集中在 `src/main.py`，使用 `motor` 访问 MongoDB、`redis.asyncio` 实现 5 分钟幂等 key。
- LLM 路径只使用 DeepSeek：OpenAI SDK，`base_url=https://api.deepseek.com`，`model=deepseek-chat`，并开启 `response_format={"type":"json_object"}`；返回值必须经过 `ParsedInfo` Pydantic schema 校验。
- 派单严格按楼栋、类型、兜底顺序。楼栋规则中的 `staff_id` 不存在时不会分配给无效人员，会继续尝试类型规则。
- Mock 通知写入 MongoDB `notifications` 集合，API 返回 `{ "sent": true, "via": "mock-bot" }`。
- CORS 允许所有来源，支持直接打开 `frontend/index.html` 访问本地服务。

## 索引

启动时创建以下索引：`tickets(parsed.building, created_at)`、`tickets(parsed.intent_type, created_at)`、`tickets(status, created_at)`、`tickets(created_at)`、`notifications(ticket_id)`。列表查询实际使用 `parsed.building` 和 `parsed.intent_type` 路径。

---

# DESUN AI 全栈工程师 · Take-home（微型物业派单系统）

> 岗位：AI 全栈工程师（DESUN，HKEX: 2270）
> 形式：48 小时内提交（建议实际投入 **4-6 小时**，不鼓励花更多时间。做不完优先砍 GET 列表过滤、索引调优等非核心项——**小而扎实 > 大而跑不起**）
> 提交：Git 仓库链接，或整个项目打包 zip 作邮件附件（**含 `.git` 提交历史**）

> 📖 **这一个文件读完即可**：先花 5 分钟看 **§0 导览**，再从 **§1** 开始正式需求；文末 **附录 A** 是 DECISIONS 模板、**附录 B** 是提交前自检清单。

---

## 0. 先读这里（导览）

### 这是什么

业主发来不固定格式的报修文本（如"3栋402卫生间漏水严重，急"），你的服务要：**LLM 解析关键字段 → 按规则派单 → mock 发通知 → 存库**。对外提供几个 HTTP 接口，`docker compose up` 一键起来即可。

### 怎么开始

1. 通读下面的需求（**§1–§9**，约 15 分钟）
2. 选你最熟的语言写后端（不限语言，但存储必须用 **MongoDB + Redis**）
3. 本地起服务后，跑 `public-tests/` 里的 3 个脚本自测（用法见 **§6**）
4. 用 `frontend/index.html`（双击打开）可视化点一下你的 API
5. 边做边记 **`DECISIONS.md`**（模板见 **附录 A**）
6. 提交前对照 **附录 B** 自检清单过一遍，按 **§9** 提交

### 包里有什么

| 文件 | 用途 |
|------|------|
| `README.md`（本文件） | 题目说明 + 完整需求 + 自检清单 + 决策模板，**全在这一份** |
| `staff.json` | 员工 / 派单配置样例数据，**请勿修改** |
| `public-tests/` | 3 个公开测试脚本（本地可跑） |
| `frontend/index.html` | 零依赖可视化测试页，**你不用写前端** |

### 三条最容易被忽略、但很关键的提醒

1. **`DECISIONS.md` 是重点**——我们想看你怎么用 AI、又怎么判断 AI 对错。诚实复盘 > 写得漂亮，全盘"采纳"无反思是扣分项。
2. **别把真实 API key 提交进 git**——`.env.example` 用占位符，`.env` 加进 `.gitignore`。
3. **鼓励用 AI 工具**（Cursor / Claude Code / Copilot 都行），但请如实记录在 `DECISIONS.md`。

有任何歧义，直接回邮件问，我们会在 FAQ 里同步答复所有候选人。祝写得开心 🙂

---

## 1. 背景

这是为一家物业公司做的微型派单服务。业主通过微信群、客服 App 等渠道发来报修文本，格式不固定，例如：

- "3 栋 402 卫生间漏水严重，急死了，赶紧来人"
- "你好麻烦报修一下，我们家是 7-1503，热水器没热水"
- "保安问下大门口那个路灯坏了好几天了"

该物业公司目前管理 30+ 个小区，每个小区按楼栋 / 工种分配负责人。你要交付的服务做四件事：

1. 收到文本 → LLM 解析出关键字段
2. 按规则匹配处理人
3. 通过 Bot 发通知（本任务中 mock 即可）
4. 持久化工单 + 通知记录

---

## 2. 你要交付的

一个可以 **`docker compose up` 一键起来** 的后端服务，对外提供 HTTP API。

### 2.1 必须实现的 API

#### `POST /tickets` — 创建工单

请求体：
```json
{
  "user_id": "u_001",
  "text": "3栋402卫生间漏水严重，急"
}
```

响应（201）：
```json
{
  "ticket_id": "tk_xxx",
  "parsed": {
    "building": 3,
    "room": "402",
    "intent_type": "plumbing",
    "urgency": "high",
    "summary": "3栋402卫生间漏水"
  },
  "assigned_to": {
    "staff_id": "s_007",
    "name": "李工",
    "match_rule": "building"
  },
  "notification": {
    "sent": true,
    "via": "mock-bot"
  }
}
```

LLM 解析失败（非法 JSON / 必填字段缺失 / `intent_type` 越枚举 / API 报错），返回 422 + 落库 `status=parse_failed`，**禁止 500**。

#### `GET /tickets/{id}` — 查询单条

#### `GET /tickets?building=3&intent_type=plumbing&status=open` — 列表查询

支持以上 3 个 query 参数任意组合，按创建时间倒序，默认返回前 50 条。

### 2.2 LLM 解析规范

输入：原始文本（中文）
输出严格 schema：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `building` | `int \| null` | 否 | 楼栋号，提取不到返回 `null` |
| `room` | `string \| null` | 否 | 房间号，保留前导零 |
| `intent_type` | `enum` | **是** | 见下表，无法判断时填 `"other"` |
| `urgency` | `enum` | 是 | `low \| normal \| high \| urgent` |
| `summary` | `string` | 是 | ≤ 50 字摘要 |

`intent_type` 枚举：
- `plumbing` — 水管、漏水、马桶、水龙头
- `electrical` — 电路、跳闸、灯具、插座
- `appliance` — 空调、热水器、家电
- `cleaning` — 保洁、垃圾、卫生
- `security` — 安保、门禁、可疑人员
- `public_facility` — 公共设施、电梯、路灯、绿化
- `complaint` — 投诉、纠纷
- `other` — 其他

**强制要求**：必须用 schema 校验（pydantic / zod / JSON schema 任选），不能直接 `json.loads` 就用。

### 2.3 派单规则

加载 `staff.json`（题面随附），按优先级匹配：

1. **楼栋归属**：staff 配置中 `buildings` 包含该楼栋号 → 直接命中（`match_rule: "building"`）
2. **类型归属**：未命中 1，按 `intent_type` 在 `dispatch_rules` 中找负责人（`match_rule: "intent_type"`）
3. **兜底**：以上都未命中，分给 `default_staff_id`（`match_rule: "fallback"`）

当一个工种 / 楼栋有多个负责人时，**随机分配一个**即可（不要求轮询）。

### 2.4 Bot 通知（mock）

不需要真的发送消息。要求：

- 写入 MongoDB `notifications` 集合，记录 `{ticket_id, staff_id, payload, sent_at}`
- 返回 `{notification.sent: true, via: "mock-bot"}`

### 2.5 幂等去重

同一 `user_id` + 文本 SHA256 在 **5 分钟内** 重复提交 → 返回已有的 `ticket_id`，HTTP 200（不是 201），不重复建单也不重发通知。

**用 Redis 实现**（不能只靠 DB unique index，要求显式的 idempotency key 设计）。

> **达标线**：`SET NX EX` 占坑 + 失败时 `GET` 拿回已有 `ticket_id` 返回，就足以通过测试。并发时序、`PENDING` 中间态、异常清理属于**加分项，不是必答**——做不到不影响过线，能想到并在 DECISIONS 里说清楚才加分。

参考实现：

```python
# 伪代码
import hashlib, time

def create_ticket(user_id, text):
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    idem_key = f"idem:tickets:{user_id}:{text_hash}"

    # 步骤 1: 尝试占坑（5 分钟过期）
    occupied = redis.set(idem_key, "PENDING", ex=300, nx=True)

    if not occupied:
        # 步骤 2: 占坑失败 → 我不是第一个，查询已有 ticket_id
        # （注意：并发场景下，前一个请求可能还在 PENDING，需要等一下重试）
        for _ in range(10):
            existing = redis.get(idem_key)
            if existing and existing != "PENDING":
                return {"ticket_id": existing, "http_code": 200}
            time.sleep(0.1)
        raise TimeoutError("idempotency lock waiting timeout")

    # 步骤 3: 占坑成功 → 我是第一个，正常建单 + LLM 解析 + 派单 + 通知
    ticket = do_create_ticket(...)

    # 步骤 4: 把真实 ticket_id 写回 Redis（覆盖 PENDING）
    redis.set(idem_key, ticket.id, ex=300)
    return {"ticket_id": ticket.id, "http_code": 201}
```

可照搬，也可用 Lua 脚本原子化等更优方案。**这段示例并不完美，建议你像 review AI 输出那样 review 它**——如有改动请在 DECISIONS.md 说明你修了什么、为什么；如果照搬也请说明你权衡过的边界（占坑失败方怎么拿 ticket_id？并发时序？异常路径？）。

### 2.6 持久化要求

- 工单存 MongoDB `tickets` 集合
- 通知存 MongoDB `notifications` 集合
- 必须有合理的索引（提交时在 README 中说明索引设计）

### 2.7 API 响应字段约定（务必对齐）

为保证评审脚本和前端 UI 能对上你的接口，以下结构**必须严格遵守**。

**`GET /tickets` 列表响应**（顶层结构固定）：
```json
{
  "items": [ /* 列表项，结构同 POST 响应，见下 */ ],
  "total": 50
}
```

**列表项字段** = `POST /tickets` 201 响应同结构（嵌套 `parsed` / `assigned_to` / `notification`），额外加：
- `status`: 见下方枚举
- `created_at`: ISO 8601 字符串（如 `"2026-05-17T08:30:00Z"`）

**`status` 字段枚举**（**只有这两种**，别造新值）：
- `open` — 正常工单（已派单 / 已通知）
- `parse_failed` — LLM 解析失败的工单

**`POST /tickets` 422 响应体**：
```json
{
  "ticket_id": "tk_xxx",
  "status": "parse_failed",
  "error": "LLM 返回非法 JSON"
}
```

> 题目刻意把状态机简化为 2 个值——你不需要做 "已完成" / "已关闭" 等状态流转。

---

## 3. 技术栈

- **语言**：不限（Python / Node.js / Go / Java / Rust 任选，推荐你最熟的）
- **存储**：必须用 MongoDB + Redis（其他可选）
- **LLM**：任选一个能调通的（OpenAI / Anthropic / 国产模型 / Ollama 本地都行）
- **容器化**：必须提供 `docker-compose.yml`，`docker compose up -d` 60 秒内服务可用

---

## 4. 提交物清单

```
your-repo/
├── README.md                ← 启动方式、API 示例、设计说明、索引说明、模型选型理由
├── DECISIONS.md             ← AI 决策日志（3-5 条，模板见附录 A）
├── docker-compose.yml
├── Dockerfile               ← (或 compose 里 build: .)
├── .env.example             ← 列出所有 env 变量（API key 用占位符）
├── src/                     ← 业务代码
├── tests/                   ← 至少 3 个测试用例
├── staff.json               ← 用我们提供的样例数据，不要改
└── (其他你觉得有必要的文件)
```

**绝对不要提交**：
- `node_modules/` / `__pycache__/` / `target/` / `dist/`
- `.env`（**真的 API key**！）
- 大于 5MB 的二进制文件
- LLM 的对话原文（请整理后写到 `DECISIONS.md`）

---

## 5. 评分维度（你看了再做心里有数）

| 维度 | 权重 | 关键看点 |
|------|------|----------|
| 后端工程基本功 | 25 | 幂等、并发、错误处理、索引 |
| LLM 应用质量 | 25 | schema 校验、兜底、prompt 设计、成本意识 |
| 业务还原度 | 15 | 第 2 节的需求是否全覆盖 |
| 工程化 | 15 | docker 一键起、README、测试 |
| **AI 决策日志** | **20** | **核心人格筛——能否复盘 AI 走偏** |

**自动化硬门槛（任一不过直接淘汰，不进入排名）：**
- 仓库包含 `docker-compose.yml` 或 `Dockerfile`
- `docker compose up -d` 60 秒内服务可用
- 公开测试通过率 ≥ 60%（我们随附了 3 个测试，你可以本地先跑）
- 提交了 `DECISIONS.md` 且 ≥ 200 字

---

## 6. 公开测试

`public-tests/` 目录下有 3 个 bash 脚本，你本地起服务后可以直接跑：

- `test_basic.sh` — 创建工单 + 校验响应结构
- `test_idempotency.sh` — 5 分钟内重复提交，校验 `ticket_id` 一致 + HTTP 200
- `test_llm_fallback.sh` — 故意发完全无意义的文本，校验是否优雅降级

**用法**（只依赖 `curl` 和 `jq`）：

```bash
# 1. 先起你的服务（默认监听 :8080，可用 BASE_URL 覆盖）
docker compose up -d

# 2. 跑公开测试
BASE_URL=http://localhost:8080 ./public-tests/test_basic.sh
BASE_URL=http://localhost:8080 ./public-tests/test_idempotency.sh
BASE_URL=http://localhost:8080 ./public-tests/test_llm_fallback.sh
```

每个脚本输出 `[PASS]` 或 `[FAIL: <原因>]`，并以对应退出码退出（0 = pass，1 = fail）。

跑通这 3 个测试只是**底线**，过了不等于满分——最终评审还会跑额外的隐藏测试用例（覆盖更严格的并发、边界与数据完整性场景）。

我们另外提供了 `frontend/index.html`（单文件零依赖 UI），双击在浏览器打开即可可视化测试你的 API。**你不需要写前端**，但你的 API 必须能被这个 UI 正确调用——视作 API 设计的隐性验收。

---

## 7. 实现提示（省时用，非强制）

**docker-compose**：app `depends_on` 加 `condition: service_healthy`，mongo/redis 配 healthcheck（最常见的 auto_reject 原因是 Mongo 没 ready 应用就崩）。或在代码里做连接 retry。

**LLM**：用便宜模型（`gpt-4o-mini` / `claude-haiku` / `deepseek-chat`）。OpenAI 用 `response_format={"type":"json_object"}`，Claude 用 tool use。必设 timeout（15s）。**不要把 staff.json 塞进 prompt**——派单是代码的事，LLM 只解析文本。

**索引**：`tickets` 至少建 `{building,created_at}` / `{intent_type,created_at}` / `{status,created_at}`，`notifications` 建 `{ticket_id}`。README 一句话说为什么这么建。

**.env**：`.env.example` 占位符 + `.gitignore` 加 `.env`。提交前 `git log -p | grep -iE 'sk-[a-z0-9]{20,}'` 自查。

**CORS**：我们提供的 `frontend/index.html` 从 `file://` 打开，调用时要允许跨域。FastAPI 用 `CORSMiddleware(allow_origins=["*"])`，Express 用 `cors()`，Gin 用 `gin-contrib/cors`。

---

## 8. AI 决策日志（DECISIONS.md）

20 分核心项。**3-5 条**，结构见 **附录 A**。

**诚实记录 > 写得漂亮。** 我们要看你**判断 AI 对错**的能力：怎么拆任务、AI 在哪跑偏、哪里你不同意 AI 的建议。

- ✅ 哪怕一条是"AI 一次写对，我做了 X 校验确认"也行——这是判断
- ❌ 全部"采纳"无反思 / "AI 帮我做了整个项目"式空洞描述 → 判为盲信，≤8/20

---

## 9. 提交方式 & FAQ

两种方式任选：**①** 仓库 public 或 invite 我方账号 → 邮件回复 repo URL；**②** 整个项目（含 `.git` 目录、去掉 `node_modules/` 等构建产物）打包 zip 作邮件附件发来。截止时间见邀请邮件。**commit message 和文件中都别暴露真实姓名**（盲评）。

| 问题 | 回答 |
|------|------|
| 能用 Cursor / Claude Code / Copilot 吗 | **鼓励**，但如实记录在 DECISIONS.md |
| LLM 调用谁付费 | 你。用便宜模型，总成本应 < $1。预算紧用 Ollama 本地 |
| 能交一个简单版吗 | 可。**小而扎实 > 大而跑不起** |
| 我想不做某项 | 在 README 写明"为什么不做"——这是判断力，不减分 |
| 有歧义问题 | 邮件问，FAQ 会同步给所有候选人 |

---

# 附录 A：DECISIONS.md 模板

> 把下面的占位符替换成你的真实记录，**3-5 条**为佳。请如实记录——评审会对照你的 commit 历史和代码核验。
> 写完后请删掉本附录里的"示例"和"模板说明"文字，`DECISIONS.md` 里只保留你自己的决策记录。

## 关于这份文档我们看什么

岗位关键词是 **"AI as primary productivity"**——能用 AI 把自己放大 3-5 倍，**且能判断 AI 输出对错**。

我们**不看**：你 prompt 写得多漂亮 / 你用了多少种 AI 工具 / "AI 帮我搞定了一切"。

我们**看**：
- 你怎么拆任务给 AI（合理颗粒度 vs 让 AI 一把梭）
- 你怎么发现 AI 跑偏（盲信 vs 校验）
- AI 不会的地方你怎么自己接手（公司内部知识、领域细节）
- **至少一处你主动否决了 AI 的建议**——这是判断力的硬证据

## 每条决策的结构

```
### 决策 N：<任务名，一句话概括>

- **场景**：<我当时在做什么、卡在哪>
- **我 prompt 了什么**：<原话或概述，可截关键片段>
- **AI 输出**：<贴关键片段，或概述 + 指出哪里有问题>
- **是否采纳**：<采纳 / 部分采纳 / 弃用>
- **理由**：<为什么这么处理。如果是部分采纳/弃用，说出你的判断依据>
- **耗时**：<大致花了多少分钟（含验证）>
```

## 示例（参考风格，写完请删）

### 决策 1：LLM 解析 prompt 的 schema 约束方式

- **场景**：第一版 prompt 用纯自然语言要求 "返回 JSON"，跑 5 条样本有 2 条返回带 markdown 代码块的 JSON，`json.loads` 直接崩
- **我 prompt 了什么**："这个 LLM 偶尔返回带 ```json 包裹的内容怎么办？给我最稳的方案"
- **AI 输出**：建议用正则 `re.search(r'\{.*\}', s, re.DOTALL)` 抽 JSON
- **是否采纳**：弃用
- **理由**：正则抽 JSON 是脏方案，遇到 nested object 会断。我换成 `response_format={"type": "json_object"}` + pydantic 校验，从根上保证结构。AI 给的能跑但是 bandage solution，不是 production-ready
- **耗时**：15 分钟（含跑样本验证）

### 决策 2：幂等 key 的设计

- **场景**：题面要求 "同 user_id + 文本 hash 在 5 分钟内重复 → 返回已有工单"
- **我 prompt 了什么**："Redis 实现幂等去重，key 怎么设计？"
- **AI 输出**：`SET idem:{user_id}:{sha256(text)} {ticket_id} EX 300 NX`
- **是否采纳**：采纳，但加了一层
- **理由**：AI 方案对了，但漏了并发下两个请求同时到、`SET NX` 一成一败，失败那个怎么拿 ticket_id？我补了：失败时立刻 `GET` 一次同 key 拿回返回
- **耗时**：20 分钟

### 决策 3：MongoDB 索引设计

- **场景**：列表查询支持 building / intent_type / status 任意组合
- **我 prompt 了什么**："tickets 集合索引怎么设计能覆盖这三种查询？"
- **AI 输出**：建议建 `{building, intent_type, status, created_at}` 一个大复合索引
- **是否采纳**：弃用
- **理由**：大复合索引只有按前缀查才有用，单独按 `status` 查就用不上。我改成三个独立索引，配合 index intersection 对任意组合都能用。前提是数据量小，规模上来再观测 explain 调整
- **耗时**：25 分钟（查了 MongoDB 文档确认行为）

> ↑ 哪怕你"几乎全采纳"，也能写出**展示判断的复盘**——关键是说清"我验证了什么、下一轮反馈是什么"。

## 想看到的关键词（出现会显著加分）

- "AI 给的代码能跑但是有 X 问题"
- "这里 AI 不知道我的项目结构，我没让它瞎猜，直接自己改了"
- "我让 AI 写测试，但它在 mock 真实场景，我手动重写了 N 条"
- "AI 建议用 X 库，我评估后用了 Y，因为 X 的 license / 维护状态 / 性能不行"
- "这部分我没让 AI 写，因为涉及 [领域知识 / 业务约束]，自己上更快"

## 不想看到的反模式

- 全部"采纳" → 盲信信号
- 全部 "AI 帮我写了 X" → 没有任何复盘，等于没写
- 决策时间线对不上 commit 历史 → 编故事
- 把 AI 失败甩锅 "AI 太笨" 而没有自己的判断 → 没有工程师视角
- 决策粒度过细（"改了个变量名"）或过粗（"做了整个项目"）

---

# 附录 B：提交前自检清单

> 提交前花 5 分钟过一遍，能避免 80% 的"明明做得不差但被淘汰"的悲剧。

**自动化硬门槛（任一不过直接淘汰）**
- [ ] 仓库根目录有 `docker-compose.yml` 或 `Dockerfile`
- [ ] 全新机器上 `git clone && docker compose up -d` 在 60 秒内服务可用
- [ ] 跑过 3 个公开测试，至少 2 个 PASS
- [ ] 仓库根目录有 `DECISIONS.md`，字数 ≥ 200

**业务必做**
- [ ] `POST /tickets` 完整：解析 → 派单 → 通知 → 持久化
- [ ] `GET /tickets/{id}` 单条查询
- [ ] `GET /tickets?building=&intent_type=&status=` 列表查询（三参数任意组合）
- [ ] LLM 解析有 schema 校验，不直接 `json.loads`
- [ ] LLM 输出非法时不让服务 500——返回 422 落库 `parse_failed`
- [ ] 派单规则按 **楼栋 > 类型 > 兜底** 优先级
- [ ] 用 Redis 做幂等去重（5 分钟内同 user_id + 同文本 → 返回旧 ticket_id + HTTP 200）
- [ ] Bot 通知写入 MongoDB `notifications` 集合（mock）
- [ ] `tickets` 和 `notifications` 集合有索引（README 中说明）

**文档与工程化**
- [ ] README 含：启动命令、API 示例（curl）、设计说明、索引说明、**LLM 模型选型理由**
- [ ] `.env.example` 列出所有环境变量（API key 用占位符）
- [ ] **没有把真实 API key 提交进 git**（用 `git log -p | grep -iE 'sk-|api[_-]key'` 自检）
- [ ] 没有提交 `node_modules/` / `__pycache__/` / `dist/` 等构建产物
- [ ] 至少 3 个测试用例（覆盖主流程 + 幂等 + LLM 兜底）
- [ ] commit 历史颗粒度合理（不是一个 "init" commit 完事）

**DECISIONS.md（重点！）**
- [ ] **3-5 条**决策记录
- [ ] 每条结构完整（场景 / prompt / AI 输出 / 是否采纳 / 理由 / 耗时）
- [ ] **至少一处明确写出"弃用"或"部分采纳"** + 理由（盲信 AI 是核心扣分项）
- [ ] 决策时间线和 commit 历史大致对得上（不要编故事）
- [ ] 没有保留模板里的"示例"和"模板说明"段落

**防御性自检**
- [ ] commit message 中**没有暴露真实姓名**（盲评）
- [ ] 在另一台机器 / 另一个文件夹下 clone 一次，从零跑一遍，确认能起来
- [ ] 如果走 **zip 邮件附件**提交：确认 zip 内**含 `.git` 目录**、**不含** `node_modules/` 等大体积构建产物
- [ ] LLM 用低成本模型（gpt-4o-mini / claude-haiku / deepseek-chat / 本地 Ollama），README 中说明
- [ ] 某项题面要求你权衡后**没做**的，README 里有 "Trade-offs" 章节说明原因（加分项，不减分）

全部勾上，你的提交已经在前 30% 了。剩下的事情我们来做。祝评审顺利。
