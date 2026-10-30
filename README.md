# RepoSense

**帮开源新手读懂一个陌生仓库，并找到自己的第一次贡献。** VS Code 扩展 + Python 后端。

1. **输入一个 GitHub 仓库链接。** RepoSense 会克隆仓库，并在 VS Code 里打开。
2. **仓库地图。** 一张可以逐层放大的地图：仓库 → 架构层（前端、后端、核心、测试、文档等）→ 目录 → 文件。方块大小代表代码量，底部细条代表近一年改动热度，每个文件都带一句话说明它负责什么。悬停一个文件，它依赖的文件和依赖它的文件会分别高亮；侧栏列出"它依赖谁 / 被谁依赖 / 对应的测试"。
   - **关系图。** 切换到关系图，当前层级的模块按 import 关系分层排列（上游在前、下游在后），只画主干依赖，悬停看某个模块的全部上下游。依赖关系由静态解析 import 得到，不经过模型。
3. **点开一个文件：**
   - **改动历史**：最近 N 次提交在代码里用不同颜色标出。鼠标悬停看是谁、何时、在哪个 PR 里改的；侧边的 git 时间线可以点开，查看 diff、PR 描述和 reviewer 意见。
   - **功能分析**：这个文件负责什么，有哪些关键函数和类（点击跳到对应行），和哪些文件配合，修改前要注意什么。
   - **贡献机会**：提到这个文件的开放 Issue，加上模型读代码后找到的适合新手的改进点。对任一 Issue 都可以一键生成贡献计划。

Claude Code 这类通用编程 Agent 擅长读懂*当前的代码*。RepoSense 的重点是代码里看不到的东西：这段代码是怎么演变来的、维护者在 review 时在意什么、哪些事已经有人试过了。

## 设计原则

- **事实不交给模型。** 提交记录、blame、PR、review、Issue 都直接来自 git 和 GitHub。模型只负责总结和归类。
- **模型的引用必须经过校验。** 模型给出的目录、文件、符号行号、代码锚点，都要和真实仓库核对，对不上就丢弃，界面上会显示丢弃了多少条。
- **分层清晰。** 见 [docs/architecture.md](docs/architecture.md)。

## 安装

需要 Python 3.11+、git、Node.js 18+（仅构建扩展时需要）。

```bash
git clone https://github.com/cty-ut/reposense.git && cd reposense
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

cd vscode-extension
npm install
npm run package                                       # 生成 reposense-<version>.vsix
code --install-extension reposense-0.2.0.vsix
```

首次使用时，扩展会依次引导：

1. **选择 Python 解释器**：选上面虚拟环境里的 Python，也就是装了 reposense 的那个。
2. **GitHub 授权**：直接用 VS Code 的 GitHub 账号登录。
3. **填写 DeepSeek API Key**：存入 VS Code 的加密存储，不写进任何文件。
4. **输入想贡献的仓库链接**。克隆完成后 VS Code 会打开这个文件夹；如果顶部出现 Restricted Mode 提示，点 **Trust** 信任它，RepoSense 才会运行。

## 命令行（调试用）

```bash
reposense call repo.structure --repo-path <本地仓库>
reposense call file.history --repo-path <本地仓库> --params '{"path": "src/app.py", "limit": 10}'
reposense call file.insight --repo-path <本地仓库> --params '{"path": "src/app.py"}'
reposense plan https://github.com/<owner>/<repo>/issues/<n>     # 针对单个 Issue 的贡献计划
```

命令行从环境变量或当前目录下的 `.env` 读取 `GITHUB_TOKEN` 和 `DEEPSEEK_API_KEY`（参考 `.env.example`）。

## 开发

```bash
pytest                                       # 后端离线测试：用临时 git 仓库和假模型，不需要网络和 key
cd vscode-extension && npm run typecheck     # 扩展类型检查
npm run watch                                # 修改界面时自动重新打包
```

## 状态与限制

- 只支持 GitHub 仓库，模型目前只支持 DeepSeek。
- 结构分析按 commit 缓存，文件分析按文件内容缓存。首次分析大仓库需要几十秒。
- 还没有定量评测；`reposense plan` 只在 click 的少量 Issue 上试过（见 `examples/`）。
