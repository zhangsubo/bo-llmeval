# OpenAI 兼容接口诊断与 AI 解读平台 - 开发指导

## 项目概览

本项目围绕 `scripts/test-openai-api.py` 与 `scripts/analyzer.py` 构建，提供 OpenAI 兼容接口（`chat/completions`、`responses`、`video`、`seedance`、`images/generations`、`bailian-video`）的命令行与 Web 可视化测试工作台，以及针对 `scripts/test-results` 的大模型智能诊断解读能力。

原 EVALPLAT 平台代码已归档至 `archive/` 目录。

## 技术栈

- **核心诊断与解读脚本**：Python 3.11+ 标准库（`scripts/test-openai-api.py`、`scripts/analyzer.py`，零第三方依赖可独立运行）
- **后端服务**：FastAPI, Uvicorn, Pydantic (`backend/`)
- **前端界面**：React 19, TypeScript, Vite, Ant Design (`frontend/`)
- **存储层**：本地文件直接持久化（`scripts/openai-api-config` 配置文件与 `scripts/test-results/` 结果目录，CLI 与 Web 实时互通，无需数据库）；常用接口和 AI 解读 API Key、素材库 AK/SK 统一使用 1Password，配置文件只保存 op:// 引用与非敏感元数据；通过 scripts/secret_store.py 调用 op CLI，不在日志、文件或命令行参数中暴露密钥

## 核心设计原则

1. **CLI 与 Web 单一数据源互通**：Web 界面直接读写 `scripts/openai-api-config` 和 `scripts/test-results/`，不引入额外数据库。
2. **独立 AI 解读模型配置**：通过 `ANALYZER_NAME`、`ANALYZER_BASE_URL`、`ANALYZER_API_KEY`、`ANALYZER_MODEL` 独立配置 OpenAI 兼容解读模型，与被测接口解耦。
3. **全方位 API Key 脱敏**：所有落盘文件（`result.json`、`summary.txt`、`analysis.json`、`analysis.md`）、终端输出及前端接口均执行脱敏。
4. **保持 `scripts/test-openai-api.py` 零外部依赖与向后兼容**：确保 `tests/test_manual_openai*.py` 全部通过。

## 常用命令

```bash
# 一键启动可视化平台并自动打开浏览器 (http://127.0.0.1:18088)
./start.sh

# 运行全量测试
./.venv/bin/python -m pytest -q

# 前端检查与构建
cd frontend && npm run lint && npm run build
```
