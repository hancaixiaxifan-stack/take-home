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
- **理由**：这能避免第二套解析实现和伪成功。最终版本只有 DeepSeek JSON mode + Pydantic schema 校验；LLM 调用失败时按题目要求返回 422 并落库 `parse_failed`。
- **耗时**：约 20 分钟。
