# DECISIONS

### 决策 1：先让 Claude 阅读题面并整理实现操作文档

- **场景**：项目初始只有题目说明、`staff.json`、公开测试脚本和前端测试页，还没有后端代码骨架。直接实现容易漏掉接口结构、幂等、派单优先级、Mongo 查询路径和 Docker healthcheck 等细节。
- **我 prompt 了什么**：我先让 Claude 阅读题目原文和已有文件，整理出一份按 Python 3.12 + FastAPI + MongoDB + Redis + DeepSeek 实现的操作文档，明确接口、数据模型、幂等流程、派单规则、容器化和验证步骤。
- **AI 输出**：Claude 给出了一份实现清单，强调所有业务逻辑放在 `src/main.py`，补齐 `Dockerfile`、`docker-compose.yml`、`.env.example`、`.gitignore` 和测试，并按公开脚本做验收。
- **是否采纳**：采纳。
- **理由**：这份文档把题目拆成可执行步骤，适合作为交给 Codex 落地实现的输入。我没有让 AI 自由扩需求，只按题面要求实现微服务。
- **耗时**：约 15 分钟。
