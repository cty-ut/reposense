# RepoSense 架构

RepoSense 由两部分组成：一个 **VS Code 扩展**（TypeScript，负责界面和编辑器交互），以及一个**常驻的 Python 后端**（负责 git、GitHub、模型调用和分析）。两者通过 **JSON-RPC over stdio** 通信，和 VS Code 语言服务器的方式一样。

```
┌──────────────────────── VS Code 扩展 ────────────────────────┐
│ features/onboarding    输入仓库链接 → 克隆 → 打开                │
│ features/repoMap       仓库地图面板 (webview)                    │
│ features/fileInsights  文件侧边面板 (webview) + 代码高亮/悬停      │
│ features/issuePlan     针对单个 Issue 的贡献计划                  │
│        │                                                     │
│ backend/rpcClient  ──── JSON-RPC (stdin/stdout) ────┐          │
└─────────────────────────────────────────────────────┼──────────┘
┌──────────────────────── Python 后端 ─────────────────▼──────────┐
│ server.py        方法分发：repo.* / file.* / commit.* / issue.*  │
│ analysis/        structure · dependencies · file_summaries ·    │
│                  file_history · file_insight · opportunities    │
│ agent/           贡献计划 Agent（探索代码 + 项目历史 + 引用校验）    │
│ core/            git · github · llm · cache · workspace · files  │
└─────────────────────────────────────────────────────────────────┘
```

## 依赖方向

只允许上层依赖下层：

- `server` → `analysis` / `agent` → `core`
- `core` 不依赖任何上层模块；`analysis` 的各个模块之间互不依赖。
- 扩展端：`features` → `backend` / `editor`。`webview` 只通过 `postMessage` 和对应的 feature 通信，不直接调用后端。

## 后端各层

| 模块 | 职责 | 是否调用模型 |
|---|---|---|
| `core/git.py` | 封装本地 git 命令：ls-files、log、blame、show | 否 |
| `core/github.py` | GitHub API：Issue、PR、review、搜索、提交对应的 PR | 否 |
| `core/llm.py` | DeepSeek 调用：JSON 模式输出、重试 | — |
| `core/cache.py` | 磁盘 JSON 缓存（按仓库 + commit 或文件内容 hash 作 key） | 否 |
| `core/workspace.py` | 克隆仓库，定位本地仓库 | 否 |
| `core/files.py` | 哪些文件参与分析（跳过二进制和 vendored 代码），地图和依赖图共用 | 否 |
| `analysis/structure.py` | 文件树统计（行数、语言、改动热度）+ 模型把目录归类到架构层，并写一句话说明 | 是，每个 commit 一次，有缓存 |
| `analysis/dependencies.py` | 静态 import 图：Python 用 `ast`（含 `__init__` 再导出、`pkg.Name` 属性引用），JS/TS 解析相对路径 import | 否，按 commit 缓存 |
| `analysis/file_summaries.py` | 每个文件一句话说明：只把大纲（docstring、类、函数）按目录分批给模型；路径必须是发出去的；Markdown 直接用标题 | 是，按文件内容缓存，只重算改过的文件 |
| `analysis/file_history.py` | 文件最近 N 次提交；用 blame 把每行映射到提交；关联 PR；单个提交的 diff 和 review 详情 | 否 |
| `analysis/file_insight.py` | 文件功能分析：职责、关键符号及其行号（行号会被校验） | 是，按文件内容缓存 |
| `analysis/opportunities.py` | 贡献机会：GitHub 上相关的开放 Issue + 模型从代码里找的改进点（引用会被校验） | 是 |
| `agent/` | 针对单个 Issue 的贡献计划 Agent | 是 |

**原则：凡是确定性的数据，都不交给模型。** 提交记录、blame、PR、Issue 都直接从 git 和 GitHub 取。模型只负责总结和归类；它输出的路径和行号都会被代码核对，对不上就丢弃。

## RPC 接口

| 方法 | 参数 | 返回 |
|---|---|---|
| `repo.clone` | `url, parent_dir` | `{root, repo}`，过程中推送 `progress` 通知 |
| `repo.open` | `path` | `{repo, root, head}` |
| `repo.structure` | `refresh?` | 地图树：`MapNode`，层级为 repo → layer → dir → file |
| `repo.dependencies` | `refresh?` | `{edges: [{source, target, weight}]}`：文件级 import 关系，weight 为引用次数 |
| `repo.summaries` | `refresh?` | `{summaries: {path: 一句话}}`，过程中推送 `progress` 通知 |
| `file.history` | `path, limit` | 最近提交列表，每个提交带它在当前文件里"留下的"行范围 |
| `commit.detail` | `sha, path` | 该文件在这次提交的 diff、PR 描述、review 意见 |
| `file.insight` | `path` | 职责、关键符号（含行号） |
| `file.opportunities` | `path` | 相关 Issue + 建议的改进点 |
| `issue.plan` | `number` | 贡献计划；过程中推送 `plan.event` 通知 |

数据类型的定义在 `vscode-extension/src/shared/protocol.ts`，和 Python 返回的字段一一对应。
