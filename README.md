# RepoSense

**给一个 GitHub 仓库地址，几分钟内搞懂整个项目。**

RepoSense 是一个 AI Agent，能自动分析开源项目的架构、回答代码相关问题、引导你上手贡献。把"读源码"这件最痛苦的事变成一次对话。

## 核心功能

### 1. 全景扫描 — `reposense analyze`

输入一个 GitHub 仓库 URL，输出项目全景报告：

- 项目类型识别（Web 应用 / CLI / 库 / 框架）
- 目录结构解读（每个目录的职责）
- 入口文件定位 + 核心模块识别
- 建议阅读顺序

```bash
reposense analyze "https://github.com/pallets/click"
```

### 2. 代码问答 — `reposense ask`

对已扫描的仓库进行多轮对话式问答：

- ReAct Agent 循环：推理 → 搜索 → 阅读 → 回答
- 混合检索：向量语义搜索 + BM25 关键词搜索 + RRF 融合
- 防幻觉：每个文件路径引用自动校验真实存在
- 多轮记忆：上下文跨轮次保持

```bash
reposense ask "https://github.com/pallets/click"
# You: Context 类是做什么的？
# You: 命令行参数是怎么解析的？
```

### 3. 贡献引导 — `reposense contribute`

自动分析 Open Issues，生成贡献路线图：

- 优先抓取 `good first issue` 等新手友好标签
- LLM 分析每个 Issue 的难度、相关代码、修改建议
- 按 🟢入门 / 🟡进阶 / 🔴挑战 分层输出

```bash
reposense contribute "https://github.com/pallets/click"
```

### 4. MCP Server — `reposense mcp-serve`

通过 MCP 协议暴露工具，可被 Cursor / Claude Desktop 等客户端调用：

```bash
reposense mcp-serve
```

## 架构

```
┌──────────────────────────────────────────────────────────┐
│                      CLI (Click + Rich)                  │
│               analyze / ask / contribute / mcp-serve     │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                   LangGraph 编排引擎                      │
│                                                          │
│  路由 → 扫描 → 三级漏斗分析 → 报告       (全景扫描)      │
│  路由 → ReAct Agent (推理→工具→观察)     (代码问答)      │
│  获取Issue → 代码关联 → LLM分析 → 路线图 (贡献引导)      │
└──────┬──────────────────────┬────────────────────────────┘
       │                     │
┌──────▼──────┐     ┌────────▼────────────────────────────┐
│  工具层      │     │  知识层 (RAG)                        │
│  GitHub API │     │  ChromaDB 向量索引 (all-MiniLM-L6-v2)│
│  文件读取    │     │  BM25 关键词索引 (rank-bm25)         │
│  代码解析    │     │  混合检索 + RRF Rerank               │
│  MCP Server │     │  索引磁盘持久化 (~/.cache/reposense) │
└─────────────┘     └─────────────────────────────────────┘
       │
┌──────▼──────┐     ┌─────────────────────────────────────┐
│  LLM 层     │     │  可观测性                            │
│  Gemini 主   │     │  Token 计数 + 成本计算               │
│  Poe 备选    │     │  节点计时 + 执行报告                  │
│  自动降级    │     │  Token 预算熔断                       │
└─────────────┘     └─────────────────────────────────────┘
```

## 技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 编排 | LangGraph | 状态图工作流 + ReAct Agent |
| 主 LLM | Gemini 2.5 Flash | 代码分析、问答（100 万 Token 上下文） |
| 备 LLM | Poe (Claude-Sonnet-4) | Gemini 限流时自动降级 |
| 数据获取 | PyGithub | GitHub REST API |
| 代码解析 | tree-sitter | Python / JS / TS AST 语义切分 |
| 向量数据库 | ChromaDB | 代码向量存储（磁盘持久化） |
| Embedding | all-MiniLM-L6-v2 | 本地 CPU 嵌入（无需外部 API） |
| 关键词检索 | rank-bm25 | 精确函数名 / 变量名匹配 |
| 检索融合 | RRF | 向量 + BM25 双路结果合并 |
| 工具协议 | MCP | 标准化工具暴露给外部客户端 |
| CLI | Click + Rich | 命令行界面 + 终端美化 |

## 快速开始

### 1. 安装

```bash
git clone https://github.com/your-username/reposense.git
cd reposense
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入 API Key：

```bash
cp .env.example .env
```

```env
GITHUB_TOKEN=ghp_your_github_token
GOOGLE_API_KEY=your_gemini_api_key      # 主 LLM
POE_API_KEY=your_poe_api_key            # 备选 LLM（可选）
```

至少需要 `GITHUB_TOKEN` + 一个 LLM API Key。

### 3. 验证配置

```bash
reposense check
```

### 4. 使用

```bash
# 全景扫描
reposense analyze "https://github.com/pallets/click"

# 代码问答
reposense ask "https://github.com/pallets/click"

# 贡献引导
reposense contribute "https://github.com/pallets/click"

# 详细日志
reposense -v analyze "https://github.com/pallets/click"
```

## 评估数据

基于 10 个不同规模和语言的开源项目（Click, httpx, LangGraph, Zustand, Zod, Cobra, Gin, FastAPI, Jest, SQLModel）进行自动化评估：

| 指标 | 结果 |
|------|------|
| Q&A 关键词命中率 | 80% |
| 文件引用有效率 | 100% |
| 防幻觉拦截 | 校验层自动标注无效引用 |

运行评估：

```bash
python eval/benchmark.py --repos click --max-questions 3
```

## 项目结构

```
src/reposense/
├── main.py              # CLI 入口 (analyze/ask/contribute/mcp-serve)
├── config.py            # 配置管理
├── llm.py               # LLM 双通道 + 自动降级 + Token 预算
├── metrics.py           # 可观测性 (计时/Token/成本/报告)
├── mcp_server.py        # MCP Server (6 个标准化工具)
├── graph/
│   ├── state.py         # RepoSenseState 状态定义
│   ├── builder.py       # LangGraph 图构建
│   ├── qa_graph.py      # QAEngine (ReAct + 索引 + 记忆)
│   ├── contribute.py    # ContributeEngine (贡献引导)
│   └── nodes/
│       ├── router.py    # 意图路由
│       ├── scanner.py   # GitHub 数据获取
│       ├── analyzer.py  # 三级漏斗 LLM 分析
│       ├── reporter.py  # 报告生成
│       ├── verifier.py  # 幻觉校验层
│       └── contributor.py # Issue 分析 + 路线图
├── tools/
│   ├── github_api.py    # GitHub API 封装
│   ├── code_parser.py   # tree-sitter 代码切分
│   └── qa_tools.py      # Agent 工具
└── rag/
    ├── indexer.py        # ChromaDB 向量索引
    ├── retriever.py      # 向量检索
    ├── bm25.py           # BM25 关键词检索
    └── hybrid.py         # 混合检索 + RRF Rerank
```

## 测试

```bash
# 运行所有单元测试 (87 cases)
pytest tests/ -v

# 运行特定模块的测试
pytest tests/test_github_api.py -v
pytest tests/test_bm25.py -v
pytest tests/test_verifier.py -v
pytest tests/test_mcp_server.py -v
pytest tests/test_metrics.py -v

# 运行 live 集成测试（需要 API Key）
pytest tests/test_contribute_live.py -v -s
```

## 设计决策

| 决策 | 选了什么 | 为什么 |
|------|---------|--------|
| 三级漏斗 vs 一次全读 | 三级漏斗 | 大仓库 Token 不可控；先粗后细成本低 |
| 向量检索 vs BM25 | 混合 + RRF | 向量擅长语义，BM25 擅长精确符号，互补 |
| 内存 vs 磁盘索引 | ChromaDB PersistentClient | 首次建索引慢，后续秒加载 |
| 单一 LLM vs 多 LLM | 双通道 + 自动降级 | Gemini 免费额度有限，Poe 兜底 |
| 直接调用 vs MCP | 两套并存 | 内部直接调用性能好，MCP 对外暴露扩展性好 |

## License

MIT
