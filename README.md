# RepoSense

**帮开源新手完成第一次贡献：从一个 Issue 出发，告诉你该改哪里、这段代码以前怎么改过、维护者在意什么。**

Claude Code、Cursor 这类通用编程 Agent 擅长读懂*当前的代码*。但新手第一次贡献时，最缺的往往是代码里看不到的东西：

- 这行代码是哪个 PR 引入的？当时为什么这么写？
- 维护者 review 类似改动时提过什么要求？
- 这个 Issue 维护者到底同不同意做？
- 改完该跑哪些测试、找谁 review？

RepoSense 把这些信息从 git 历史、PR 讨论和 review 意见里找出来，连同具体的修改位置，一起标注在 VS Code 里你正在看的代码上。

## 工作方式

```
VS Code 扩展 (TypeScript)                     Python Agent (reposense)
├─ 侧边栏：贡献计划                ◄── JSON ──  ├─ 代码探索：glob / grep / read / ls
├─ 代码行标注：建议修改 + 历史 PR      事件流     ├─ 项目历史：git log / blame / PR 与 review / Issue 搜索
└─ 面板：Agent 实时轨迹 + PR 时间线              └─ 引用校验：不通过就退回给 Agent 修正
```

- **不建索引，直接探索代码。** 参考 Claude Code 的做法：Agent 在本地仓库里用 grep、glob 定位，只读需要的行范围。没有向量库，代码切换到任何版本都能直接搜。
- **差异化在项目历史。** `git blame` 找到目标代码出自哪个 PR，再读这个 PR 的讨论和 review 意见，提炼出新手应该遵守的约定。
- **引用必须经过校验。** Agent 提交计划时，代码会逐条核对：
  - 引用的行号要存在；
  - 引用的原文要和文件对得上；
  - 那段代码 Agent 必须真的读过；
  - 提到的 PR 必须真的查过；
  - 推荐的 reviewer 必须在查到的数据里出现过。

  任何一条不通过，都会带着错误信息退回给 Agent 修正。最终仍无法通过的条目会被删掉，并单独列出，不会混进计划里。
- **限制写在代码里，不只写在提示词里。** 轮数上限、工具调用上限、token 预算、重复调用检测，都由代码强制执行。上下文平时只追加，以便命中模型服务端的前缀缓存；超过阈值时才压缩旧的工具结果。

## 快速开始

需要 Python 3.11+、git，最好也装上 [ripgrep](https://github.com/BurntSushi/ripgrep)（没装时自动改用 `git grep`）。

```bash
git clone https://github.com/cty-ut/reposense.git && cd reposense
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env   # 填写 GITHUB_TOKEN 和 DEEPSEEK_API_KEY
reposense check
```

### 命令行

```bash
# 自动克隆仓库到 ~/.cache/reposense，生成贡献计划
reposense plan https://github.com/pallets/click/issues/3652

# 使用已有的本地仓库；保存完整结果
reposense plan https://github.com/pallets/click/issues/3652 --repo-path ../click --out plan.json

# 对照组：关闭项目历史工具，只做代码探索
reposense plan <issue-url> --no-history
```

### VS Code 扩展

```bash
cd vscode-extension
npm install && npm run compile
npx vsce package --allow-missing-repository --skip-license
code --install-extension reposense-0.1.0.vsix
```

1. 在 VS Code 里打开你想贡献的仓库。
2. 在设置里把 `reposense.pythonPath` 设为装了 reposense 的 Python 解释器。
3. 点左侧活动栏的 RepoSense 图标，选 **Analyze Issue**，输入 Issue 编号。

## 当前状态

- [x] 代码探索与项目历史工具、Agent 循环、引用校验
- [x] VS Code 扩展：侧边栏计划、代码行标注、悬停详情、实时轨迹面板
- [ ] 评测：用历史上已关闭的 Issue 及其修复 PR 作为标准答案，比较开启和关闭历史工具时的文件命中率
- [ ] 支持 DeepSeek 以外的模型

## 开发

```bash
pytest            # 离线测试：用临时 git 仓库和脚本化的假模型，不需要网络和 API Key
```

License: MIT
