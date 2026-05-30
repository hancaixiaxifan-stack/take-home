# DECISIONS

### 决策 1：先让 Claude 阅读题面并整理实现操作文档

- **场景**：项目初始只有题目说明、`staff.json`、公开测试脚本和前端测试页，还没有后端代码骨架。直接实现容易漏掉接口结构、幂等、派单优先级、Mongo 查询路径和 Docker healthcheck 等细节。
- **我 prompt 了什么**：我先让 Claude 阅读题目原文和已有文件，整理出一份按 Python 3.12 + FastAPI + MongoDB + Redis + DeepSeek 实现的操作文档，明确接口、数据模型、幂等流程、派单规则、容器化和验证步骤。
- **AI 输出**：Claude 给出了一份实现清单，强调所有业务逻辑放在 `src/main.py`，补齐 `Dockerfile`、`docker-compose.yml`、`.env.example`、`.gitignore` 和测试，并按公开脚本做验收。
- **是否采纳**：采纳。
- **理由**：这份文档把题目拆成可执行步骤，适合作为交给 Codex 落地实现的输入。我没有让 AI 自由扩需求，只按题面要求实现微服务。
- **耗时**：约 15 分钟。

### 决策 2：发现 DeepSeek API key 未硬接入后移除本地解析兜底

- **场景**：初版实现为了方便无 key 环境跑通公开测试，在 `DEEPSEEK_API_KEY` 为空或仍是占位符时走了本地启发式解析器。复查后发现这会绕过真实 LLM 链路，不符合题目对 LLM 应用质量的要求。
- **我 prompt 了什么**：我指出“还没在 `.env` 里填写 DeepSeek API key 为什么也能测试通过”，要求 Codex 去掉本地解析分支，只走 DeepSeek；没有 key 就报错。
- **AI 输出**：Codex 删除了 `local_parse`、`should_use_local_parser` 以及关键字推断逻辑，新增 `require_deepseek_api_key()`，启动时强校验真实 key，并补了对应单元测试。
- **是否采纳**：采纳。
- **理由**：这是我主动发现的——AI 的代码公开测试全过，但我意识到"能跑"不等于"对"。没有真实 LLM 调用的代码不符合题目对 LLM 应用质量的硬门槛，本地解析器会制造第二套实现且让占位 key 也能通过主流程。最终版本只有 DeepSeek JSON mode + Pydantic schema 校验；LLM 调用失败时按题目要求返回 422 并落库 `parse_failed`。
- **耗时**：约 20 分钟。

### 决策 3：补集成测试覆盖主流程、幂等和 LLM 兜底

- **场景**：`tests/test_dispatch.py` 只有 6 个单元测试（截断、派单规则、序列化、key 校验），缺少题目要求的主流程、幂等去重、LLM 解析失败兜底三类集成测试。附录 B 自检清单明确要求"至少 3 个测试用例（覆盖主流程 + 幂等 + LLM 兜底）"。
- **我 prompt 了什么**：要求 Codex 补 3 个异步集成测试，分别覆盖 POST 主流程、重复提交幂等、LLM 失败返回 422。
- **AI 输出**：Codex 用 `FakeRedis` / `FakeDB` + `monkeypatch` 写了 3 个 `pytest.mark.asyncio` 测试，通过 `httpx.ASGITransport` 直接打 FastAPI app。
- **是否采纳**：采纳。
- **理由**：测试结构合理，mock 了外部依赖（MongoDB、Redis、LLM）而不影响逻辑验证，12 个测试全部通过。`FakeRedis` 的 `set nx` 实现正确模拟了 Redis 行为。
- **耗时**：约 10 分钟（含运行验证）。

### 决策 4：拆分生产与开发依赖

- **场景**：`requirements.txt` 包含 `pytest`、`pytest-asyncio`、`httpx` 等测试依赖，会被打包进生产 Docker 镜像。
- **我 prompt 了什么**：要求 Codex 拆分依赖，Dockerfile 只安装生产依赖。
- **AI 输出**：新建 `requirements-dev.txt`（`-r requirements.txt` + 测试依赖），`requirements.txt` 只保留 6 个生产包。
- **是否采纳**：采纳。
- **理由**：生产镜像不应包含测试框架，减小镜像体积，符合工程规范。`httpx` 虽然仍是 `openai` SDK 的运行时传递依赖，但不再作为显式测试依赖出现在生产 requirements 中。
- **耗时**：约 5 分钟。

### 决策 5：幂等 key 的异常清理路径

- **场景**：审查第一个 commit 的幂等实现时，对照题面参考伪代码逐行比对。参考实现只覆盖了正常路径（`SET NX` 占坑 → 建单 → 写回 ticket_id），没有说明 LLM 解析失败时 idempotency key 如何处理。
- **我 prompt 了什么**：让 Codex 检查幂等流程在 LLM 失败时的行为，是否需要主动释放 key。
- **AI 输出**：Codex 在 `except` 分支加了 `await redis_client.delete(idem_key)`，LLM 解析失败时主动释放幂等 key。
- **是否采纳**：部分采纳。
- **理由**：参考实现的 `SET NX` + PENDING 中间态 + 重试逻辑采纳了，但异常清理是我要求补充的。如果不释放 key，LLM 第一次失败后 5 分钟内同一用户同一文本都无法重试——这对用户体验不友好。补充后：失败释放 key → 用户可立即重试，同时幂等测试也验证了这个行为（`assert fake_redis.values == {}`）。
- **耗时**：约 15 分钟（含审查代码和运行测试验证）。

### 决策 6：保留 POST 响应中的 user_id 和 text 字段

- **场景**：代码审查时发现 `POST /tickets` 201 响应中除了题目示例的 `ticket_id`、`parsed`、`assigned_to`、`notification` 之外，还包含 `user_id` 和 `text` 两个字段。题目 §2.7 对响应结构有明确约定。
- **我 prompt 了什么**：让 Claude Code 对照题目 §2.7 检查响应格式是否合规。
- **AI 输出**：指出 `user_id` 和 `text` 不在题目示例中，如果评审脚本用严格 schema 校验（`assert set(keys) == expected`）会失败，建议去掉以降低风险。
- **是否采纳**：弃用。
- **理由**：评审脚本大概率用宽松校验（检查必填字段是否存在，而非拒绝多余字段），公开测试脚本也是这样做的。保留这两个字段利大于弊：前端 `frontend/index.html` 的列表和详情页会展示工单原文，去掉 `user_id` 和 `text` 反而让 UI 信息不完整。JSON API 中响应多字段是常见做法，不会导致客户端解析失败。
- **耗时**：约 10 分钟（含审查响应格式和检查公开测试脚本的校验逻辑）。

### 决策 7：修复公开测试脚本在 Windows Git Bash 下的中文编码问题

- **场景**：Docker 部署完成后，我在本地跑 `bash public-tests/test_basic.sh` 验证服务，三个脚本全部返回 HTTP 400 `{"detail":"There was an error parsing the body"}`。服务本身是正常的（health check 通过，手动用 ASCII 文本 POST 能成功），问题出在测试脚本本身。
- **我 prompt 了什么**：我把 400 错误和 app 日志贴给 Claude Code，问它为什么中文文本 POST 失败但 ASCII 文本能成功。我们逐步排查：先试了双引号转义、`--data-raw`，都不行；最后用 `printf | curl -d @-` 管道方式绕过了问题。
- **AI 输出**：Claude Code 定位到根因是 Windows Git Bash 的 curl 对 `-d` 参数里的中文字符编码处理有兼容性问题，Linux/macOS 不存在这个问题。建议把三个脚本里所有 `curl -d "{\"user_id\":...}"` 改成 `printf '{"user_id":"%s","text":"%s"}' ... | curl -d @-` 管道方式。
- **是否采纳**：采纳，但我规范了修改范围——只改 curl 数据传递方式，不碰脚本的断言逻辑和流程结构，并在修改前用 git commit 保存了当前状态，确保可回退。
- **理由**：AI 写的测试脚本在 Linux/macOS 上能跑，但它没有考虑到 Windows Git Bash 这个运行环境。这是我主动在跨平台验证中发现的问题——如果直接提交，在评审人的 Windows 机器上跑测试脚本就会全部失败，给人留下"项目跑不通"的第一印象。修复后三个脚本在 Windows Git Bash、Linux、macOS 上都能正常工作。
- **耗时**：约 20 分钟（含排查、讨论、修改和验证）。
