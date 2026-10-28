<div align="center">

# RepoSense

**Understand any open-source project in minutes, not hours.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![LangGraph](https://img.shields.io/badge/Built%20with-LangGraph-purple.svg)](https://github.com/langchain-ai/langgraph)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek-blue.svg)](https://platform.deepseek.com/)

给 RepoSense 一个 GitHub 仓库地址，它帮你在几分钟内搞懂项目架构、核心逻辑和贡献路径。

把 **"读源码"** 这件最痛苦的事变成一次对话。

[快速开始](#-快速开始) · [功能演示](#-核心功能) · [架构设计](#-架构) · [评测数据](#-评测数据) · [工作流文档](WORKFLOW.md)

</div>

---

## 🎯 解决什么问题

面对一个陌生的开源项目：

| 传统方式 | RepoSense |
|---------|-----------|
| 手动翻几百个文件猜结构 | **一条命令**输出全景报告 |
| 在 GitHub 上逐文件搜索 | **自然语言对话**，Agent 自动检索 |
| 看 Issue 不知道改哪个文件 | **自动关联代码**，按难度推荐 |
| 通常需要 6-20 小时 | **不到 10 分钟** |

---

## ✨ 核心功能

### 1️⃣ 全景扫描

```bash
reposense analyze "https://github.com/tiangolo/sqlmodel"
```

> 3 分钟生成一份完整的项目架构报告：项目类型、目录职责、核心模块、入口文件、学习路径。

### 2️⃣ 代码问答

```bash
reposense ask "https://github.com/tiangolo/sqlmodel"
```

> ReAct Agent 自动推理 → 搜索代码 → 阅读文件 → 回答问题。支持多轮对话，每个引用自动验证。

```
You: SQLModel 是怎么把 Pydantic 和 SQLAlchemy 结合在一起的？

RepoSense: SQLModel 通过多重继承实现融合，核心在 `sqlmodel/main.py`：
  1. SQLModel 类同时继承 BaseModel 和 DeclarativeMeta...
  2. 在 `sqlmodel/_compat.py:45` 通过 __init_subclass__ 钩子...
  (iterations=3, refs=4/4 valid)

You: 那 Session 是怎么管理的？

RepoSense: Session 管理在 `sqlmodel/engine/create.py` 中...
```

### 3️⃣ 贡献引导

```bash
reposense contribute "https://github.com/tiangolo/sqlmodel"
```

> 自动分析 Issue，生成按难度分级的贡献路线图，告诉你该改哪个文件、怎么开始。

```
🟢 入门级
  Issue #789: 添加 UUID 字段默认值
  相关文件: sqlmodel/main.py, sqlmodel/fields.py
  建议: 参考现有 Field() 实现，添加 default_factory...

🟡 进阶级
  Issue #456: 支持异步 Session
  ...
```

输出路线图后还能继续追问：`#789 能再详细说说吗？`

### 4️⃣ MCP Server

```bash
reposense mcp-serve
```

> 通过 Model Context Protocol 暴露工具，可被 Claude Desktop / Cursor 等 AI 客户端直接调用。

---

## 🚀 快速开始

```bash
# 安装
git clone https://github.com/cty-ut/reposense.git && cd reposense
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 配置（填入你的 API Key）
cp .env.example .env

# 验证
reposense check

# 开始使用
reposense analyze "https://github.com/pallets/click"
```

<details>
<summary><b>📋 配置说明</b></summary>

在 `.env` 中填入：

```env
GITHUB_TOKEN=ghp_xxxx              # 必填
DEEPSEEK_API_KEY=sk-...            # 主力 LLM (deepseek-chat)
GOOGLE_API_KEY=AIzaSy...           # 备用 LLM (可选，Gemini 2.5 Flash)
POE_API_KEY=xxx                    # 备用 LLM (可选)
```

至少需要 `GITHUB_TOKEN` + 一个 LLM API Key。多个 Key 同时配置时按 DeepSeek → Gemini → Poe 的顺序使用，前一个限流时自动降级。

</details>

---

## 📊 评测数据

基于 `tiangolo/sqlmodel` 的完整评测（[查看报告](eval/results/eval_20260407_030720.md)，当时使用 Gemini 2.5 Flash，尚未用 DeepSeek 重新评测）：

| 指标 | 结果 | 目标 |
|------|------|------|
| 项目类型准确率 | **100%** | ≥ 85% |
| 核心目录召回率 | **100%** | ≥ 70% |
| Q&A 关键词命中率 | **86%** | ≥ 80% |
| 文件引用有效率 | **100%** | ≥ 90% |
| 幻觉率 | **0%** | ≤ 10% |
| 鲁棒性通过率 | **100%** | ≥ 90% |
| 多轮对话连贯率 | **100%** | ≥ 80% |
| 贡献引导文件引用 | **100%** | ≥ 80% |

| 效率指标 | 数值 |
|---------|------|
| 全景扫描 | ~3.5 分钟 |
| Q&A 单次回答 | 5-10 秒 |
| 索引冷启动 | ~30 秒 |
| 索引热加载 | **0.1 秒**（加速 585x） |
| 单次分析成本 | ~$0.04（约 ¥0.28） |

<details>
<summary><b>🧪 自己跑评测</b></summary>

```bash
# 快速评测（单仓库）
.venv/bin/python eval/full_evaluation.py --repos sqlmodel

# 指定其他仓库
.venv/bin/python eval/full_evaluation.py --repos click

# 只跑某个维度
.venv/bin/python eval/full_evaluation.py --only qa,robustness
```

评测覆盖 5 个维度：质量、效率、鲁棒性、多轮连贯、贡献引导。报告保存在 `eval/results/`。

</details>

---

## 🏗 架构

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI (Click + Rich)                       │
│              analyze  /  ask  /  contribute  /  mcp-serve    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    LangGraph 编排引擎                         │
│                                                              │
│  全景扫描:  路由 → 扫描 → 三级漏斗分析 → 报告                 │
│  代码问答:  ReAct Agent (推理 → 工具调用 → 观察 → 回答)       │
│  贡献引导:  获取Issue → 代码关联 → LLM分析 → 路线图           │
└────────┬─────────────────────────┬──────────────────────────┘
         │                        │
┌────────▼────────┐    ┌──────────▼──────────────────────────┐
│    工具层        │    │   知识层 (RAG)                       │
│  · GitHub API   │    │  · ChromaDB 向量索引                 │
│  · 文件读取      │    │  · BM25 关键词索引                   │
│  · tree-sitter  │    │  · RRF 混合检索 + Rerank             │
│  · MCP Server   │    │  · 磁盘持久化缓存                    │
└────────┬────────┘    └─────────────────────────────────────┘
         │
┌────────▼────────┐    ┌─────────────────────────────────────┐
│    LLM 层       │    │   可观测性                           │
│  · DeepSeek (主)│    │  · Token 计数 + 成本追踪             │
│  · Gemini/Poe(备)│   │  · 速率限制冷却                      │
│  · 自动降级     │    │  · Token 预算熔断                    │
│  · 60s 冷却     │    │  · 幻觉校验 (否认检测)               │
└─────────────────┘    └─────────────────────────────────────┘
```

---

## 🔑 设计亮点

| 设计 | 方案 | 为什么 |
|------|------|--------|
| **三级漏斗分析** | 目录级 → 文件级 → 函数级，逐层深入 | 大仓库不可能一次全读，先粗后细控制 Token |
| **混合检索 + RRF** | 向量语义搜索 + BM25 关键词搜索 | 向量擅长理解意图，BM25 擅长精确匹配，互补 |
| **Zipball 加速** | 一次性下载仓库压缩包，替代逐文件 API | 索引构建从 10 分钟缩短到 30 秒 |
| **多 LLM 降级** | DeepSeek → Gemini → Poe，60 秒冷却机制 | 限流或额度用完自动切换，不中断分析 |
| **两层反幻觉** | Prompt 约束 + 后验路径校验 | LLM 会编造文件路径，必须双重保障 |
| **索引持久化** | ChromaDB + BM25 pickle 缓存到磁盘 | 首次 30 秒，再次 0.1 秒 |

---

## 📁 项目结构

<details>
<summary><b>展开查看</b></summary>

```
src/reposense/
├── main.py                 # CLI 入口
├── config.py               # 配置管理
├── llm.py                  # 多 LLM 通道 + 自动降级 + Token 预算
├── metrics.py              # 可观测性
├── mcp_server.py           # MCP Server
├── graph/                  # LangGraph 工作流
│   ├── state.py            # 状态定义
│   ├── builder.py          # 全景扫描图
│   ├── qa_graph.py         # ReAct Q&A 引擎
│   ├── contribute.py       # 贡献引导引擎
│   └── nodes/              # 图节点
│       ├── router.py       # 意图路由
│       ├── scanner.py      # GitHub 数据采集
│       ├── analyzer.py     # 三级漏斗 LLM 分析
│       ├── reporter.py     # 报告生成
│       ├── verifier.py     # 幻觉校验
│       └── contributor.py  # Issue 分析 + 路线图
├── rag/                    # 检索增强生成
│   ├── indexer.py          # ChromaDB 向量索引
│   ├── retriever.py        # 向量检索
│   ├── bm25.py             # BM25 关键词检索
│   └── hybrid.py           # 混合检索 + RRF Rerank
└── tools/                  # 工具层
    ├── github_api.py       # GitHub API (含 zipball 下载)
    ├── code_parser.py      # tree-sitter 代码切分
    └── qa_tools.py         # Agent 工具定义
```

</details>

---

## 📖 文档

- **[WORKFLOW.md](WORKFLOW.md)** — 完整工作流文档（架构详解、数据流、使用指南）
- **[PRD.md](PRD.md)** — 产品需求文档
- **[DEVPLAN.md](DEVPLAN.md)** — 开发排期（7 个 Sprint）
- **[PROGRESS.md](PROGRESS.md)** — 开发日志

---

## License

MIT
