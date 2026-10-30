# RepoSense Agent 完整工作流文档

## 一、系统总览

RepoSense 是一个基于 LangGraph 构建的 AI Agent，能够通过一个 GitHub 仓库地址，在几分钟内完成项目结构分析、代码问答和贡献引导。系统包含三条核心工作流：

```
用户输入 GitHub URL
       │
       ├──→ reposense analyze  ──→ 全景扫描工作流 ──→ 项目架构报告
       │
       ├──→ reposense ask      ──→ ReAct Q&A 工作流 ──→ 对话式代码问答
       │
       └──→ reposense contribute ──→ 贡献引导工作流 ──→ Issue 分析 + 贡献路线图
```

### 技术栈

| 组件 | 技术选型 | 职责 |
|------|---------|------|
| 工作流编排 | LangGraph (StateGraph) | 定义节点和边，驱动多步分析流程 |
| 主力 LLM | Gemini 2.5 Flash | 代码分析、问答推理、报告生成 |
| 备用 LLM | Poe API (Claude-Sonnet-4) | Gemini 不可用时自动降级 |
| 向量数据库 | ChromaDB (本地嵌入) | 存储代码语义向量，支持语义检索 |
| 嵌入模型 | all-MiniLM-L6-v2 | ChromaDB 内置的本地 CPU 嵌入模型 |
| 关键词检索 | rank-bm25 | 基于 BM25 的关键词匹配 |
| 代码解析 | tree-sitter | 按语法结构（函数、类）切分代码块 |
| GitHub 数据 | PyGithub | 获取仓库元数据、文件树、文件内容、Issue |
| CLI | Click + Rich | 命令行界面和终端美化输出 |
| 外部接口 | MCP Server (stdio) | 将工具暴露为 Model Context Protocol 服务 |

---

## 二、工作流一：全景扫描（`reposense analyze`）

### 2.1 流程概览

```
START → router → scanner → file_reader → level1_analyzer
     → level2_analyzer → deep_reader → level3_analyzer → reporter → END
```

这是一个线性 LangGraph 图，共 8 个节点。所有中间数据存储在一个共享的 `RepoSenseState`（TypedDict）中，在节点之间流转。

### 2.2 各节点详细说明

#### 节点 1：Router（路由）

**文件：** `src/reposense/graph/nodes/router.py`

**输入：** 用户提供的 GitHub URL（如 `https://github.com/tiangolo/sqlmodel`）

**处理：**
1. 从 URL 中解析出 `owner/repo` 格式的仓库名
2. 判断用户意图（scan / ask / contribute）
3. 验证 URL 格式，无效则写入 `state["error"]` 并终止

**输出：** `repo_name: "tiangolo/sqlmodel"`, `intent: "scan"`

#### 节点 2：Scanner（数据采集）

**文件：** `src/reposense/graph/nodes/scanner.py`

**处理：**
1. 调用 `GitHubClient.get_repo_meta()` 获取仓库元信息：
   - 名称、描述、主语言、所有语言及占比
   - Star 数、Fork 数、Open Issue 数
   - 默认分支、创建/更新时间、Topics、License
   - 贡献者数量
2. 调用 `GitHubClient.get_file_tree()` 获取完整目录树：
   - 使用 Git Tree API（单次递归调用）
   - 如果树被截断（大仓库），自动降级为逐目录遍历

**输出：** `repo_meta: {...}`, `file_tree: [{path, type, size}, ...]`

#### 节点 3：File Reader（签名文件读取）

**文件：** `src/reposense/graph/nodes/scanner.py` → `file_reader_node`

**处理：**
1. 从文件树中挑选"签名文件"——最可能帮助理解项目的文件：
   - 入口文件：`main.py`, `app.py`, `index.ts`, `server.py` 等
   - 配置文件：`pyproject.toml`, `package.json`, `Cargo.toml` 等
   - 初始化文件：`__init__.py`, `__main__.py`
   - 顶层 README
2. 通过 GitHub API 读取每个签名文件的前 50 行作为预览
3. 将预览内容缓存到 `state["file_contents"]` 中，避免后续重复请求

**输出：** `file_contents: {"path/to/file.py": "前50行内容", ...}`

#### 节点 4：Level 1 Analyzer（目录级分析）

**文件：** `src/reposense/graph/nodes/analyzer.py` → `level1_analyzer_node`

**处理：**
1. 构造 prompt：将仓库元信息 + 完整文件树传给 LLM
2. LLM 分析产出（JSON 格式）：
   - `directory_roles`: 每个顶层目录的职责描述
   - `project_type`: 项目类型（Web 应用 / CLI 工具 / 库 / 框架等）
   - `core_directories`: 3-8 个核心逻辑目录
   - `entry_points`: 入口文件列表
3. 如果 LLM 返回的不是合法 JSON，使用 `_extract_json()` 鲁棒解析（正则匹配最外层 `{}`，最多重试 2 次）

**LLM 调用：** 1 次

**输出：** `directory_roles`, `project_type`, `core_directories`, `entry_points`

#### 节点 5：Level 2 Analyzer（文件级摘要）

**文件：** `src/reposense/graph/nodes/analyzer.py` → `level2_analyzer_node`

**处理：**
1. 将所有已读文件的预览内容拼接传给 LLM
2. LLM 分析产出：
   - `file_summaries`: 每个文件的一句话摘要
   - `key_files`: 5-10 个需要深入阅读的关键文件（优先源码，排除测试和配置）

**LLM 调用：** 1 次

**输出：** `file_summaries: {"path": "摘要"}`, `key_files: ["path1", "path2"]`

#### 节点 6：Deep Reader（深度读取）

**文件：** `src/reposense/graph/nodes/scanner.py` → `deep_reader_node`

**处理：**
1. 根据 Level 2 识别的 `key_files`，通过 GitHub API 读取其**完整内容**
2. 替换之前缓存的 50 行预览为完整源码
3. 已有完整内容的文件跳过

**输出：** 更新 `file_contents` 中关键文件的内容为完整版本

#### 节点 7：Level 3 Analyzer（深度代码分析）

**文件：** `src/reposense/graph/nodes/analyzer.py` → `level3_analyzer_node`

**处理：**
1. 将关键文件的完整源码传给 LLM
2. LLM 对每个文件进行深度分析：
   - 文件的核心目的
   - 关键类/函数的功能
   - 与项目其他部分的关联关系

**LLM 调用：** 1 次

**输出：** `key_file_analyses: {"path": "详细分析"}`

#### 节点 8：Reporter（报告生成）

**文件：** `src/reposense/graph/nodes/reporter.py`

**处理：**
1. 将前面所有分析结果（元信息、目录角色、文件摘要、深度分析）汇总传给 LLM
2. LLM 生成一份结构化的中文 Markdown 项目全景报告，包含：
   - 项目概览（名称、一句话总结、语言、规模）
   - 技术栈
   - 架构分析（核心模块及其关系）
   - 入口文件说明
   - 数据流/请求流
   - 学习路径建议

**LLM 调用：** 1 次

**输出：** 最终的 Markdown 报告

### 2.3 全景扫描数据流

```
GitHub URL
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│                   RepoSenseState                        │
│                                                         │
│  router 写入 → repo_name, intent                        │
│  scanner 写入 → repo_meta, file_tree                    │
│  file_reader 写入 → file_contents (50行预览)             │
│  level1 写入 → directory_roles, project_type,           │
│               core_directories, entry_points            │
│  level2 写入 → file_summaries, key_files                │
│  deep_reader 更新 → file_contents (完整内容)             │
│  level3 写入 → key_file_analyses                        │
│  reporter 写入 → report (最终 Markdown)                  │
│                                                         │
└─────────────────────────────────────────────────────────┘
    │
    ▼
终端输出 Markdown 报告
```

### 2.4 LLM 调用总结

全景扫描共调用 LLM **4 次**：
1. Level 1：目录结构 → 项目类型 + 核心目录
2. Level 2：文件预览 → 文件摘要 + 关键文件
3. Level 3：完整源码 → 深度分析
4. Reporter：所有分析 → 最终报告

---

## 三、工作流二：ReAct Q&A（`reposense ask`）

### 3.1 启动流程

```
用户输入 GitHub URL
    │
    ▼
1. 解析 repo_name
2. 获取 repo_meta + file_tree (GitHub API)
3. 构建代码搜索索引：
   a. 下载仓库 zipball (一次性获取所有文件)
   b. tree-sitter 按函数/类切分代码块
   c. ChromaDB 向量索引 (语义检索)
   d. BM25 关键词索引 (精确匹配)
4. 缓存索引到 ~/.cache/reposense/
5. 进入交互式问答循环
```

### 3.2 索引构建详细流程

```
GitHub Zipball API
    │
    ▼ 一次性下载仓库压缩包并解压
    │
过滤可索引文件（.py, .js, .ts, .go 等，排除二进制和 .lock）
    │
    ▼
tree-sitter 语义切分
    │  每个文件按语法结构切成代码块：
    │  - 函数级别: def foo(): ...
    │  - 类级别: class Bar: ...
    │  - 模块级别: 顶层代码
    │  每块记录: file_path, start_line, end_line, symbol_name, symbol_type, language
    │
    ├──→ ChromaDB 向量索引
    │    使用 all-MiniLM-L6-v2 嵌入模型
    │    按余弦相似度建索引
    │    持久化到 ~/.cache/reposense/chroma/
    │
    └──→ BM25 关键词索引
         对代码块文本分词
         构建倒排索引
         序列化到 ~/.cache/reposense/bm25_owner_repo.pkl
```

### 3.3 ReAct 循环详细流程

ReAct（Reason → Act → Observe）是 Agent 的核心推理范式。`QAEngine` 使用 LangGraph 的 `create_react_agent` 构建 ReAct agent，每轮问答的流程如下：

```
用户提问: "SQLModel 是怎么定义模型的？"
    │
    ▼
┌─────────────────────────────────────────────┐
│            ReAct 循环（最多 N 轮）            │
│                                             │
│  第 1 轮:                                   │
│  [Reason] 我需要搜索 SQLModel 的模型定义方式   │
│  [Act]    semantic_search("SQLModel model   │
│           definition")                      │
│  [Observe] 返回 6 个相关代码块:              │
│           sqlmodel/main.py L15-80,          │
│           sqlmodel/_compat.py L1-30, ...    │
│                                             │
│  第 2 轮:                                   │
│  [Reason] 找到了核心文件，需要看完整内容       │
│  [Act]    read_file("sqlmodel/main.py")     │
│  [Observe] 返回 main.py 完整源码 (300行)     │
│                                             │
│  第 3 轮:                                   │
│  [Reason] 信息足够，可以回答了               │
│  [Final Answer] SQLModel 通过继承           │
│    SQLModel 类来定义模型...                  │
│                                             │
└─────────────────────────────────────────────┘
    │
    ▼
幻觉校验 (Verifier)
    │  提取答案中的文件路径引用
    │  对照 file_tree 检查是否真实存在
    │  标注不存在的路径
    │
    ▼
输出带引用的回答
```

### 3.4 Agent 可用工具

| 工具 | 功能 | 何时使用 |
|------|------|---------|
| `semantic_search(query)` | 混合检索：向量语义搜索 + BM25 关键词搜索，通过 RRF 融合排序 | 不确定在哪个文件时，用自然语言搜索相关代码 |
| `read_file(file_path)` | 从 GitHub 直接读取指定文件的完整内容 | 已知文件路径，需要查看具体实现 |
| `list_files(directory)` | 列出指定目录下的文件和子目录 | 探索仓库结构，了解某个模块包含哪些文件 |

### 3.5 混合检索（Hybrid Retrieval）

`semantic_search` 工具内部同时执行两路检索，通过 Reciprocal Rank Fusion（RRF）融合排序：

```
用户查询: "database session handling"
    │
    ├──→ 向量检索 (ChromaDB)
    │    query 经 all-MiniLM-L6-v2 编码为向量
    │    余弦相似度 Top-K 匹配
    │    擅长: 语义相近但用词不同的内容
    │    例: 搜 "database session" 能找到 "engine.connect()" 
    │
    ├──→ BM25 检索
    │    query 分词后倒排索引匹配
    │    TF-IDF 加权排序
    │    擅长: 精确关键词、函数名、类名
    │    例: 搜 "Session" 精确匹配代码中的 Session 类
    │
    ▼
RRF 融合: score(d) = Σ 1/(K + rank_i(d))
    │  K = 60 (标准 RRF 常数)
    │  两路结果按 RRF 分数重排序
    │  同时出现在两路的结果得分更高
    │
    ▼
返回 Top-6 代码块
每个结果标注 source: "vector" / "bm25" / "both"
```

### 3.6 多轮对话

- 使用 LangGraph 的 `MemorySaver` 作为 checkpointer
- 同一个 `thread_id` 下的所有消息共享上下文
- Agent 能引用之前对话中的信息（如 "你刚才提到的那个函数"）
- 用户退出后，内存中的索引保持可用；重新进入需要重新启动

### 3.7 幻觉校验（Verifier）

**文件：** `src/reposense/graph/nodes/verifier.py`

每次 Agent 回答后，Verifier 自动执行：

1. **路径提取：** 用正则从回答中提取所有文件路径引用（反引号包裹的 `path/file.ext` 或裸路径）
2. **否认检测：** 判断路径是否出现在否认语境中（如 "不存在"、"not found"），如果是则跳过（不算引用）
   - 使用子句边界（句号、"but"、"however"等）切分，防止前一个子句的否认影响后一个子句的有效引用
3. **路径校验：** 将提取的路径与 `file_tree` 对照，检查是否真实存在
4. **结果标注：** 计算幻觉率，如果有无效路径，在回答末尾添加警告

---

## 四、工作流三：贡献引导（`reposense contribute`）

### 4.1 完整流程

```
用户输入 GitHub URL
    │
    ▼
Step 1: 获取仓库元数据 + 文件树
    │   GitHubClient.get_repo_meta()
    │   GitHubClient.get_file_tree()
    │
    ▼
Step 2: 获取 Issue
    │   优先获取带有入门标签的 Issue:
    │   "good first issue", "help wanted", "beginner",
    │   "easy", "starter", "first-timers-only" 等
    │   最多获取 20 个 Issue
    │
    ▼
Step 3: 构建代码搜索索引
    │   复用 QAEngine 的索引构建流程
    │   向量索引 + BM25 索引
    │
    ▼
Step 4: 逐个分析 Issue（最多 10 个）
    │   对每个 Issue:
    │   ┌──────────────────────────────────────────┐
    │   │ a. 用混合检索搜索 Issue 相关代码          │
    │   │ b. 将 Issue 内容 + 搜索结果 + 文件树      │
    │   │    传给 LLM 分析                          │
    │   │ c. LLM 返回 JSON:                        │
    │   │    - difficulty: "easy"/"medium"/"hard"   │
    │   │    - summary: 问题概述                    │
    │   │    - related_files: 相关文件路径           │
    │   │    - suggestion: 如何开始修复             │
    │   │ d. _validate_file_refs() 后验校验:        │
    │   │    只保留 file_tree 中真实存在的文件路径   │
    │   └──────────────────────────────────────────┘
    │
    ▼
Step 5: 生成贡献路线图
    │   将所有 Issue 分析结果传给 LLM
    │   生成结构化的中文 Markdown 路线图:
    │   - 🟢 入门级 Issue
    │   - 🟡 进阶级 Issue
    │   - 🔴 挑战级 Issue
    │   - 开始贡献的步骤
    │
    │   _sanitize_roadmap_paths() 后处理:
    │   扫描 Markdown 中的所有反引号路径
    │   移除不在 file_tree 中的虚假路径
    │
    ▼
Step 6: 输出路线图 + 进入 Follow-up Q&A
    │   用户可以追问某个 Issue 的细节
    │   复用 ReAct Q&A 工作流
```

### 4.2 反幻觉机制（两层校验）

贡献引导模块的文件路径需要特别严格的校验，因为 LLM 容易编造不存在的文件路径：

**第一层：Issue 分析 JSON 校验（`_validate_file_refs`）**
- 对 LLM 返回的 `related_files` 数组逐路径检查
- 只保留 `file_tree` 中 `type == "blob"` 的真实文件
- 支持文件名模糊匹配（如 LLM 给了 `main.py`，自动匹配为 `src/app/main.py`）
- 不通过的路径被过滤并记录日志

**第二层：Roadmap Markdown 校验（`_sanitize_roadmap_paths`）**
- 对最终生成的 Markdown 报告进行正则扫描
- 提取所有反引号中的文件路径
- 不存在于 `file_tree` 中的路径被移除
- 确保用户看到的每一个文件引用都是真实可访问的

---

## 五、LLM 调用层（`src/reposense/llm.py`）

### 5.1 双提供商自动降级

```
invoke_with_fallback(messages)
    │
    ▼
检查 Token 预算 (max_tokens_per_analysis)
    │  超限 → 抛出 RuntimeError 终止分析
    │
    ▼
┌─────────────────────────────────────────┐
│ Provider 1: Gemini 2.5 Flash            │
│   检查是否在 60s 冷却期 → 跳过          │
│   最多重试 3 次，指数退避               │
│   遇到 429 → 标记冷却 60s → 立即切换    │
├─────────────────────────────────────────┤
│ Provider 2: Poe (Claude-Sonnet-4)       │
│   同样逻辑: 3 次重试 + 冷却机制         │
│   两个都失败 → 抛出 RuntimeError        │
└─────────────────────────────────────────┘
    │
    ▼
记录到 MetricsCollector:
  provider, model, input_tokens, output_tokens,
  elapsed_s, success, error
```

### 5.2 速率限制冷却机制

```python
# 当收到 429 / RESOURCE_EXHAUSTED 错误时
_rate_limited_until["Gemini"] = time.time() + 60  # 冷却 60 秒

# 后续调用检查
if time.time() < _rate_limited_until["Gemini"]:
    # 跳过 Gemini，直接用 Poe
```

避免了反复尝试已耗尽配额的提供商，大幅减少了无效等待时间。

---

## 六、可观测性（`src/reposense/metrics.py`）

### 6.1 MetricsCollector

全局单例，记录整个会话的性能指标：

| 指标类别 | 记录内容 |
|---------|---------|
| LLM 调用 | provider, model, input/output tokens, 耗时, 成本, 成功/失败 |
| ReAct Agent | 通过 `_MetricsCallbackHandler` 回调自动记录每次 agent 内部的 LLM 调用 |
| 节点执行 | 每个 LangGraph 节点的执行耗时 |
| GitHub API | API 调用次数 |

### 6.2 Token 成本计算

```
Gemini 2.5 Flash: $0.15/M input + $0.60/M output
Poe:              $0.003/M input + $0.015/M output
```

### 6.3 Token 预算控制

配置 `max_tokens_per_analysis`（默认 500,000），当累计 token 超限时自动终止 LLM 调用，防止失控消耗。

---

## 七、缓存策略

### 7.1 索引缓存

| 缓存对象 | 存储位置 | 加载条件 |
|---------|---------|---------|
| ChromaDB 向量索引 | `~/.cache/reposense/chroma/` | Collection 存在且非空 |
| BM25 关键词索引 | `~/.cache/reposense/bm25_owner_repo.pkl` | Pickle 文件存在 |

首次构建约 30 秒（zipball 下载 + 切分 + 编码），缓存命中后 < 0.1 秒。

### 7.2 文件内容缓存

`GitHubClient` 内部有内存级缓存，同一会话中对同一文件的重复读取不会产生额外 API 调用。

### 7.3 Zipball 加速

`download_repo_files()` 使用 GitHub Archive API 一次性下载仓库 zip 压缩包并解压，比逐文件通过 Contents API 获取快 **20 倍以上**。失败时自动降级为逐文件下载。

---

## 八、MCP Server（`reposense mcp-serve`）

通过 Model Context Protocol 将 RepoSense 的能力暴露为标准工具接口，允许外部 AI 客户端（如 Claude Desktop、Cursor）直接调用：

| MCP 工具 | 对应内部功能 |
|---------|-------------|
| `get_repo_meta` | `GitHubClient.get_repo_meta()` |
| `get_file_tree` | `GitHubClient.get_file_tree()` |
| `get_file_content` | `GitHubClient.get_file_content()` |
| `get_issues` | `GitHubClient.get_issues()` |
| `search_code` | `HybridRetriever.search()` |
| `index_repo` | `CodeIndexer.build_index_from_github()` + BM25 |

通过 stdio 传输协议通信，无需网络端口。

---

## 九、评测体系（`eval/full_evaluation.py`）

### 9.1 评测维度

| 维度 | 指标 | 目标 |
|------|------|------|
| **质量** | 项目类型准确率 | ≥ 85% |
| | 核心目录召回率 | ≥ 70% |
| | 入口文件召回率 | ≥ 60% |
| | Q&A 关键词命中率 | ≥ 80% |
| | 文件引用有效率 | ≥ 90% |
| | 幻觉率 | ≤ 10% |
| **效率** | 全景扫描耗时 | 监控 |
| | Q&A 平均耗时 | 监控 |
| | ReAct 循环次数 | 监控 |
| | Token 消耗 / 成本 | 监控 |
| **鲁棒性** | 畸形输入处理 | 100% |
| | 错误恢复 | 100% |
| | 对抗性查询 | ≥ 90% |
| | 缓存行为 | 100% |
| | 边界条件 | 100% |
| **多轮** | 对话连贯率 | ≥ 80% |
| **贡献** | 引导成功率 | ≥ 80% |
| | 文件引用有效率 | 100% |

### 9.2 报告内容

评测报告包含：
- 评分总览表（每项指标 vs 目标，✅/⚠️/❌ 状态）
- 全景扫描详情
- Q&A 详情（含每个问题的工具调用明细）
- 鲁棒性测试用例结果
- 多轮对话连贯性
- 贡献引导文件引用校验
- 效率指标（LLM 调用次数、Token、成本、耗时）

---

## 十、项目文件结构

```
src/reposense/
├── __init__.py                    # 版本号
├── __main__.py                    # python -m reposense 入口
├── main.py                        # CLI 定义 (Click)
├── config.py                      # 配置管理 (API keys, 模型参数)
├── llm.py                         # LLM 调用层 (Gemini/Poe 双降级)
├── metrics.py                     # 可观测性 (token, 耗时, 成本)
├── mcp_server.py                  # MCP Server (stdio)
│
├── graph/                         # LangGraph 工作流
│   ├── state.py                   # RepoSenseState 定义
│   ├── builder.py                 # 全景扫描图构建
│   ├── qa_graph.py                # ReAct Q&A 引擎
│   ├── contribute.py              # 贡献引导引擎
│   └── nodes/                     # 图节点实现
│       ├── router.py              # URL 解析 + 意图路由
│       ├── scanner.py             # GitHub 数据采集
│       ├── analyzer.py            # 三级漏斗分析 (L1/L2/L3)
│       ├── reporter.py            # 报告生成
│       ├── contributor.py         # Issue 分析 + 路线图生成
│       └── verifier.py            # 幻觉校验
│
├── rag/                           # 检索增强生成
│   ├── indexer.py                 # ChromaDB 向量索引
│   ├── retriever.py               # 向量检索器
│   ├── bm25.py                    # BM25 关键词检索
│   └── hybrid.py                  # RRF 混合检索
│
└── tools/                         # 工具层
    ├── github_api.py              # GitHub API 客户端 (含 zipball 下载)
    ├── code_parser.py             # tree-sitter 代码切分
    └── qa_tools.py                # ReAct Agent 工具定义

eval/
├── full_evaluation.py             # 全面评测脚本
├── test_repos.json                # 测试仓库 + ground truth
└── results/                       # 评测报告输出目录
```

---

## 十一、用户使用指南 & 实际体验

### 11.1 安装与配置（一次性，约 2 分钟）

```bash
# 1. 克隆项目
git clone <repo-url> && cd agent

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
source .venv/bin/activate
pip install -e .

# 3. 配置 API Key（复制模板，填入你的 key）
cp .env.example .env
# 编辑 .env，填入：
#   GITHUB_TOKEN=ghp_xxxx          （必填）
#   GOOGLE_API_KEY=AIzaSy...       （Gemini，主力 LLM）
#   POE_API_KEY=xxx                （Poe，备用 LLM，可选）

# 4. 验证配置
reposense check
# ✓ GITHUB_TOKEN
# ✓ GOOGLE_API_KEY
# ✓ POE_API_KEY (fallback: Claude-Sonnet-4)
```

### 11.2 场景一：快速了解一个陌生项目

> **痛点：** 你发现了一个感兴趣的开源项目，有几百个文件，README 写得很简略，不知道从哪看起。
>
> **传统方式：** 手动翻目录、猜结构、读代码，通常需要 **6-20 小时**。

```bash
reposense analyze https://github.com/tiangolo/sqlmodel
```

**等待约 3 分钟**，你会得到一份完整的项目全景报告：

```
## 项目概览
- 名称：tiangolo/sqlmodel
- 一句话总结：基于 Pydantic 和 SQLAlchemy 的 Python ORM 库，
  让数据模型定义和数据库操作用同一个类完成
- 语言：Python (95%)
- Stars: 14.2k

## 架构分析
- sqlmodel/main.py: 核心入口，定义了 SQLModel 基类...
- sqlmodel/engine/: 数据库引擎和 Session 管理...
...

## 学习路径建议
1. 先看 sqlmodel/main.py 理解 SQLModel 类的定义
2. 然后看 sqlmodel/engine/create.py 理解数据库连接
3. ...
```

**3 分钟 vs 6-20 小时，效率提升 100 倍以上。**

### 11.3 场景二：对代码有具体问题，想深入了解

> **痛点：** 看完全景报告后，你对某些实现细节有疑问，想知道具体怎么写的。
>
> **传统方式：** 在 GitHub 上一个个文件点开搜索，或者 clone 下来用 IDE 全局搜索。

```bash
reposense ask https://github.com/tiangolo/sqlmodel
```

**首次约等 30 秒建索引**（之后同一仓库秒加载），然后直接对话：

```
You: SQLModel 是怎么把 Pydantic 和 SQLAlchemy 结合在一起的？

RepoSense: SQLModel 通过多重继承实现了 Pydantic 和 SQLAlchemy 的融合。
核心逻辑在 `sqlmodel/main.py` 中：

1. `SQLModel` 类同时继承了 `pydantic.BaseModel` 和 SQLAlchemy 的
   `DeclarativeMeta`...
2. 在 `sqlmodel/_compat.py:45` 中，通过 `__init_subclass__` 钩子
   自动将 Pydantic 字段映射为 SQLAlchemy Column...

(iterations=3, refs=4/4 valid)
```

```
You: 那 Session 是怎么管理的？

RepoSense: Session 管理在 `sqlmodel/engine/create.py` 中实现...
```

**Agent 会自动搜索代码、读取文件、引用具体行号，你只需要用自然语言提问。**

支持多轮对话，Agent 记得你之前问过什么：

```
You: 你刚才说的那个钩子能展开讲讲吗？

RepoSense: 好的，`__init_subclass__` 在 `sqlmodel/main.py:128` 中定义...
```

### 11.4 场景三：想给开源项目贡献代码，但不知道从哪下手

> **痛点：** 你想参与贡献，打开 Issue 列表有几十个，不知道哪个适合自己，也不知道该改哪些文件。
>
> **传统方式：** 逐个读 Issue，猜关联代码，反复试错，往往放弃。

```bash
reposense contribute https://github.com/tiangolo/sqlmodel
```

**约 2-3 分钟**，你会得到一份量身定制的贡献路线图：

```
✓ Repository: SQL databases in Python, designed for simplicity
✓ Found 20 issues
✓ Index built: 787 chunks
✓ Analyzed 10 issues: 3 easy, 5 medium, 2 hard

## 贡献路线图

### 🟢 入门级（Easy）

#### Issue #789: 添加对 UUID 字段的默认值支持
- **概述：** 用户希望 UUID 字段能自动生成默认值
- **相关文件：** `sqlmodel/main.py`, `sqlmodel/fields.py`
- **建议：** 参考 `sqlmodel/fields.py` 中现有的 Field() 实现，
  添加 default_factory=uuid4 的支持
- **预计时间：** 1-2 小时

### 🟡 进阶级（Medium）
...
```

每个 Issue 都告诉你：该改哪个文件、怎么开始、预计多久。

路线图输出后，**不会退出**，而是自动进入 Follow-up 问答模式，你可以继续追问任何 Issue 的细节：

```
You can now ask follow-up questions about any issue
(e.g. 'Tell me more about issue #123'). Type 'q' to exit.

You: #789 这个 Issue 能再详细说说吗？

RepoSense: Issue #789 需要在 `sqlmodel/fields.py` 的 Field() 函数中
添加 UUID 默认值支持。具体来说：
1. 在 `sqlmodel/fields.py:82` 的 FieldInfo 构造中...
2. 需要参考 `sqlmodel/main.py:156` 中现有的类型映射...
(iterations=2, refs=2/2 valid)

You: 现在这个项目的测试怎么跑？

RepoSense: 项目使用 pytest 作为测试框架，配置在 `pyproject.toml` 中...

You: q
Goodbye!
```

追问时复用同一套索引和对话记忆，**不需要重新等待**，每个问题 5-10 秒即可回答。提到 `#数字` 会自动关联对应 Issue 的分析上下文，普通问题则走通用代码问答。

### 11.5 完整使用流程示例

一个典型的使用场景——你想参与 `pallets/click` 这个项目的开源贡献：

```bash
# Step 1: 先快速了解项目（3 分钟）
reposense analyze https://github.com/pallets/click
# → 得到项目全景报告，了解 Click 是一个 CLI 框架

# Step 2: 对感兴趣的部分深入提问（索引秒加载，因为 Step 1 已缓存）
reposense ask https://github.com/pallets/click
You: Click 的命令注册机制是怎么实现的？
You: @click.command() 装饰器内部做了什么？
You: Group 和 Command 的关系是什么？
# → 每个问题 5-10 秒得到带代码引用的回答

# Step 3: 找一个适合自己的 Issue 开始贡献（索引继续复用）
reposense contribute https://github.com/pallets/click
# → 得到按难度分级的贡献路线图
```

**三个命令、总共不到 10 分钟，你就从"完全不了解"变成"知道项目怎么运作、该从哪里开始贡献"。**

### 11.6 性能数据（基于实际评测）

| 指标 | 数值 |
|------|------|
| 全景扫描耗时 | ~3.5 分钟 |
| Q&A 单次回答 | 5-10 秒 |
| 索引冷启动 | ~30 秒 |
| 索引热加载 | ~0.1 秒（加速 585x） |
| 单次分析成本 | ~$0.04（约 ¥0.28） |
| Q&A 关键词命中率 | 86%（目标 ≥80%） |
| 文件引用有效率 | 100%（目标 ≥90%） |
| 幻觉率 | 0%（目标 ≤10%） |
| 鲁棒性通过率 | 100% |
| 多轮对话连贯率 | 100% |
| 贡献引导文件引用 | 100% |

### 11.7 MCP 集成（高级用法）

如果你使用支持 MCP 的 AI 客户端（如 Claude Desktop、Cursor），可以将 RepoSense 作为工具服务接入：

```bash
reposense mcp-serve
```

这样你的 AI 助手就能直接调用 RepoSense 的能力——获取仓库信息、搜索代码、读取文件——而无需离开对话界面。

### 11.8 评测（开发者用）

```bash
# 快速评测（单仓库，约 7 分钟）
.venv/bin/python eval/full_evaluation.py --repos sqlmodel

# 指定其他仓库
.venv/bin/python eval/full_evaluation.py --repos click
.venv/bin/python eval/full_evaluation.py --repos zod

# 全量评测（所有预定义仓库）
.venv/bin/python eval/full_evaluation.py --full

# 只跑某个测试维度
.venv/bin/python eval/full_evaluation.py --only qa
.venv/bin/python eval/full_evaluation.py --only robustness

# 报告自动保存到 eval/results/ 目录
```
