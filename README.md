# Coding Agent (Forge)

本地 Coding Agent 的 Monorepo：以文件夹为入口，支持可审计、可恢复的修改与验证，Git 发布为可选能力。

2026-10-24 核心实现与回归已收口：489 个独立 API 用例去重覆盖为 487 通过、2 项 Windows 符号链接环境跳过；真实模型完成单文件修复、两文件修改与故障注入后的修复，并显式确认完成证据。真实 GitHub 链路已完成审批提交、推送、[Draft PR #1](https://github.com/sarffff/ai-agent/pull/1) 创建和远端核对；完整浏览器点击仍被本机权限策略阻断。结果、预算边界与证据口径见 [核心 Agent 验收报告](docs/core-agent-acceptance-20261024.md)。

2026-10-31 的功能修正已通过当前 API 全套回归：568 passed、2 项 Windows 符号链接环境跳过，570 项去重核对无遗漏；隔离真实 HTTP 验收 6/6 通过。会话输入隔离、执行前准备、Cowork 取消重试、模型门禁和桌面后端恢复的结果及未验证范围见[功能修正报告](docs/functional-fixes-20261031.md)。

同日后续完善了聊天执行摘要、工具结果、Markdown 阅读、上下文与预算展示，以及停止和刷新反馈。这批修改的本地检查与保留边界见[聊天展示完善报告](docs/chat-presentation-20261031.md)；真实使用与视觉验收由用户完成，上述 API/HTTP 数字属于前一批功能修正。

上一批已补齐 Cowork 流式回复、已发送附件卡片、会话搜索/重命名、重启草稿恢复和聊天内本轮差异；同时修正刷新失败后连续发送、迟到创建、重命名和删除目录的会话归属。该批完整本地 API 回归 600 passed / 2 skipped，602 项去重覆盖无遗漏；桌面 9 组检查、类型、构建和产物检查通过。实现范围与草稿恢复方式见[聊天功能补齐报告](docs/chat-features-20261031.md)。

前一批完善 Agent Coding 核心：修正最后一次调用回复的应用/验证流程、回滚及重启预算计时、异常模型响应和损坏备份回滚；增加命令写盘后的停止检测与选中上下文缺失提示。该批完整本地 API 回归 658 passed / 2 skipped，660 项收集和执行去重覆盖无遗漏；新增 58 项核心回归全部通过，桌面类型、聊天/工具结果检查、构建及产物检查通过。历史结果见[核心编码完善报告](docs/agent-coding-improvements-20261031.md)。

2026-11-01 继续补齐每任务独立工作区、命令修改预览与重启恢复、大文件区段修改、嵌套目录规则、计划步骤与完成证据，以及普通目录和无需改动任务的本地完成。新建 Code 默认在独立目录执行，页面展示来源与执行路径；验证使用的未提交依赖超出任务范围时，阻止提交与发布。完整 API 回归 808 passed / 3 skipped，811 项收集与执行去重无遗漏；新增 151 个用例，原 660 个全部保留。类型、9 组桌面组件检查、构建、产物和 Python 语法检查通过。实现与使用边界见[剩余功能补齐报告](docs/agent-gap-completion-20261031.md)，真实模型、页面点击与视觉验收由用户完成。

## 目录

```text
apps/
  desktop/   React + Vite + TypeScript 工作台
    electron/  默认桌面入口（main / backend 生命周期 / preload / config）
  api/       FastAPI 服务（仓库接入、编码循环、审批、Git、远程发布、审计）
packages/
  types/     前后端共享领域类型
  ui/        共享组件（Dialog、AsyncMessage）
docs/
  architecture.md
  phase-1-todolist.md
  phase-2-todolist.md
  phase-3-todolist.md
examples/
  phase-1-demo/   演示仓库模板源文件
scripts/
  create_demo.py  生成隔离的本地演示 Git 仓库
  init_state.py   创建/检查本地状态存储
  py.js           优先使用仓库 .venv 的 Python 启动器
  desktop_smoke.js 校验 Desktop 构建产物
  electron.js     Electron 桌面壳启动器（start / dev / smoke）
```

## 环境要求

- Node.js 20+，pnpm 12
- Python 3.11+（当前仓库 .venv 本地验证使用 3.12.13）
- 可用的 `git` 命令行

## 安装与启动

```bash
pnpm install
python -m venv .venv && .venv/Scripts/pip install -r apps/api/requirements-dev.txt   # Windows
pnpm api:test        # 脚本会自动优先使用 .venv 中的解释器

pnpm start          # 构建前端并启动 Electron，桌面托管本地 API
pnpm api:dev        # 只启动 API：http://127.0.0.1:8000
pnpm dev            # 启动 Electron 与 Vite 开发服务器，桌面托管 API
```

`scripts/py.js` 与 `run.js` 会在存在 `.venv` 时使用其中的 Python，否则回落到 `python`/`python3`。

## 配置

| 变量 | 位置 | 作用 | 默认值 |
|---|---|---|---|
| `FORGE_WORKSPACE_ROOT` | `apps/api/.env` | 默认工作区根目录（桌面壳据此给默认目录）。仓库可以是磁盘上任意文件夹，API 只拒绝盘符与文件系统根 | 进程启动目录 |
| `FORGE_STATE_DIR` | 同上 | 状态存储目录：`state.db` 与 `checkpoints/` | `.forge/state` |
| `FORGE_MAX_FILE_SIZE_BYTES` | 同上 | 单文件读写上限 | 1000000 |
| `FORGE_MAX_TREE_ENTRIES` / `FORGE_MAX_SEARCH_RESULTS` | 同上 | 树与搜索的返回上限 | 5000 / 200 |
| `FORGE_GIT_TIMEOUT_SECONDS` / `FORGE_COMMAND_TIMEOUT_SECONDS` | 同上 | Git 与验证命令超时；后者是单条检查 `timeout_seconds` 的上限 | 10 / 300 |
| `FORGE_REMOTE_PROVIDER` | 同上 | 远程 Provider：`github` 或离线的 `fake`（演示/测试用） | `github` |
| `FORGE_GITHUB_TOKEN` | 同上 | GitHub REST 令牌，用于仓库校验与 PR 创建；只进请求头 | 空 |
| `FORGE_ALLOWED_REMOTE_HOSTS` | 同上 | 允许推送/建 PR 的远程主机（JSON 数组） | `["github.com"]` |
| `FORGE_ALLOWED_REPOSITORIES` | 同上 | `owner/repo` 白名单（JSON 数组），为空表示不额外限制 | `[]` |
| `FORGE_REMOTE_TIMEOUT_SECONDS` | 同上 | 远程 REST 调用与推送超时 | 30 |
| `FORGE_IDEMPOTENCY_TTL_HOURS` | 同上 | `Idempotency-Key` 记录保留时长 | 24 |
| `FORGE_CHECKPOINT_MAX_PER_TASK` | 同上 | 每个任务保留的检查点数；超出的最旧检查点删除备份、保留记录并标记「已清理」，不可再回滚 | 20 |
| 输入长度 | — | 任务目标/聊天消息 1–32,000 字符（空串拒绝），分支名 1–100，提交信息 1–2,000，补丁说明 ≤4,000；前后端同一口径 | — |
| `FORGE_LLM_PROVIDER` | 同上 | 补丁起草 Provider：`anthropic` / `openai` / 离线 `fake`（仅测试，需显式 seed） | `anthropic` |
| `FORGE_ANTHROPIC_API_KEY` / `FORGE_OPENAI_API_KEY` | 同上 | 对应 Provider 的 API key，用于补丁起草；只进请求头 | 空 |
| `FORGE_LLM_MODEL` | 同上 | 起草模型，须与 provider 匹配（anthropic 可设 `claude-opus-5-5`，openai 设 gpt 模型；其余 `FORGE_LLM_*`/`FORGE_OPENAI_API_URL` 见 `.env.example`） | `claude-sonnet-5-5` |
| `FORGE_CORS_ORIGINS` | 同上 | 允许访问 API 的源 | `http://localhost:5173`、`http://127.0.0.1:5173` |
| `VITE_API_BASE_URL` | `apps/desktop/.env` | 浏览器开发模式的 API 地址；Electron 使用托管地址 | `http://127.0.0.1:8000` |

复制 `apps/api/.env.example` 与 `apps/desktop/.env.example` 为 `.env` 后再修改。

设置页（账号菜单「设置」）可以保存多组「供应商 + 模型」，切换使用中的一组；切换会重建模型实例并写入 `config.json`，下次启动沿用。模型请求、编码循环或手动起草仍在运行时，服务端会在保存之前拒绝影响模型的改动；停止并等待请求退出后再切换。设置页还能改 API Key、接口地址、模型/命令超时和上下文上限。改动写入 `FORGE_STATE_DIR/config.json`（仅本人可读），启动时覆盖 `.env`；密钥只回显「已配置 / 未配置」，不会出现在界面、审计事件或错误响应里。保留期限、任务预算和工作区根仍是只读项，需要改环境变量并重启。

> CORS 默认只放行 5173。Desktop 运行在其他端口时必须把该端口加入 `FORGE_CORS_ORIGINS`，否则请求会被浏览器拦截，界面表现为“没有仓库、没有任务”。桌面壳不需要这一步——它会把自己的来源注入 `FORGE_CORS_ORIGINS`。

## 桌面壳（Electron）

`pnpm start` 与 `pnpm desktop:electron` 会构建前端、**拉起并托管本地 API**，然后打开原生窗口。`pnpm dev` 与 `pnpm desktop:electron:dev` 使用 Vite 热更新。需要单独运行 API 时使用 `pnpm api:dev` 或 `node run.js backend`。

```bash
pnpm desktop:electron        # 构建产物 + 托管 API + 打开窗口
pnpm desktop:electron:dev    # 连同 vite 开发服务器启动（HMR，默认 5173）
pnpm desktop:electron:smoke  # 隐藏窗口交互自检：14 条断言，全过才退出码 0
```

自检覆盖的是**真窗口里才成立**的那些事，而不是静态检查：窗口加载地址与 `location.origin` 都是 `forge-app://desktop`、CSS 子资源也经协议 handler 取到、渲染进程直接 `fetch` 壳托管的 API（这才是那条 CORS 边界的实证）、React 受控输入写进目标框后「发送」由 disabled 变可用、端口记忆已落盘、以及选择器之后的完整链路——侧栏登记文件夹 → 选中 → 文件树 → 读到正文标记串。

自检模式使用**可丢弃的根**（`userData/state-smoke` 与 `~/forge-workspace-acceptance`，每次启动先清空），因为 API 没有注销仓库的接口，测试数据必须与真实桌面状态隔离。原生目录选择器会弹模态框、把自动检查死锁住，所以这三项仍需人工：原生选择器、「换工作区」确认框、深色主题对比度。

壳负责的事情：

- **进程生命周期**：用 `.venv` 里的解释器（可用 `FORGE_PYTHON` 指定）以 `python -m uvicorn app.main:app --app-dir apps/api` 启动，只绑定 `127.0.0.1` 的临时端口；退出窗口时等待子进程结束。启动失败或异常退出会显示错误与重试入口。
- **端口记忆与认领校验**：端口记在 `%LOCALAPPDATA%/Forge Coding Agent/forge-desktop.json`。重启优先沿用同一端口；若被占用则换端口，每次前端请求读取最新托管地址。后端未就绪时请求会明确失败，不回落到其他本地 API。
- **来源与凭据边界**：窗口通过特权自定义协议 `forge-app://desktop` 加载产物（`file://` 的 Origin 是 `null`，CORS 无法放行），并把 `forge-app://desktop` 注入 `FORGE_CORS_ORIGINS`。渲染进程 `contextIsolation + sandbox + 无 nodeIntegration`，只暴露 `window.forge` 的少量方法；外链交给系统浏览器，禁止 `<webview>`。
- **状态位置**：`state.db` 与检查点落在 `userData/state`，后端日志在 `userData/logs/backend.log`（面板里可一键打开），不再依赖启动上下文的工作目录。
- **工作区根目录**：壳用原生目录选择器维护 `FORGE_WORKSPACE_ROOT`（默认 `~/forge-workspace`），更换会重启后端且保持端口不变。它只是默认起点：仓库本身可以在磁盘任意位置，API 只拒绝盘符与文件系统根。

| 变量 | 作用 |
|---|---|
| `FORGE_PYTHON` | 指定后端解释器，优先于仓库 `.venv` |
| `FORGE_ELECTRON_DEV_PORT` | `desktop:electron:dev` 使用的 vite 端口（默认 5173） |
| `FORGE_API_ROOT` | 后端源码所在的仓库根目录；源码运行时自动推导，打包版必须显式提供 |
| `FORGE_ELECTRON_SMOKE` / `FORGE_ELECTRON_DEV_URL` | 自检模式 / 开发模式加载地址（由脚本设置） |

> **Windows 上的一个已知前置条件**：Electron 启动时会检查 `node_modules/.../electron/dist` 是否允许 AppContainer 读取。若工作区处于受限账户 ACL 之下（本仓开发环境就是这样），会看到
> `FATAL:install_dir_access.cc ... none for ALL APPLICATION PACKAGES`，此时任何 Electron 应用（包括空应用）都起不来，与本项目代码无关。在**你自己的终端**里执行一次即可：
> `icacls "node_modules\.pnpm\electron@<版本>\node_modules\electron\dist" /grant *S-1-15-2-1:(OI)(CI)(RX)`
> 补上这条 ACE 后桌面壳即可正常启动——本仓开发环境就是这样：`icacls` 由操作者在自己的终端执行一次，之后 `pnpm desktop:electron:smoke` 退出码 0。窗口层已按上面的 14 条断言真机验收过。
>
> 关于这道检查，实测过两件事，别再花时间试第三条路：
> - **`--no-sandbox` 在 Electron 44 上无效**。这个 FATAL 来自 Electron 自己的 `install_dir_access.cc`，与渲染进程是否沙箱化无关（`--no-sandbox`、`--disable-gpu`、`ELECTRON_DISABLE_SANDBOX=1` 全部实测照旧）。
> - **降级到没有这道检查的旧版（如 31.x）确实能开窗口**，但要同时带 `--no-sandbox`——旧版没检查，沙箱子进程照样读不到 `dist`，只是换成 GPU 进程 `0xC0000135`（STATUS_DLL_NOT_FOUND）后 FATAL。也就是说代价是**放弃渲染进程沙箱**加上一个落后的 Chromium，不是免费的绕行。补那条 ACE 才是恢复配置，而不是削弱它。

## 打开文件夹

从侧栏或输入框旁点击「打开本地文件夹」：Electron 直接弹出系统目录选择器，选中后打开并进入 Code 新会话；浏览器显示绝对路径输入框。已打开的文件夹在侧栏独立分组，即使还没有任务也能新建对话；输入框旁的「项目或文件夹」菜单可切换已有目录，并查看完整路径。目录可以是磁盘上任意文件夹（盘符与文件系统根除外），打开失败会保留所选路径，桌面端可展开「工作区设置」更换默认根目录。文件浏览页的「移除」只把文件夹从列表里摘掉，磁盘上的文件不会被删除；如果该文件夹下还有任务，会再确认一次是否一并删除。

普通文件夹不需要 `git init`：可以浏览与搜索文件、运行 Code 的 AI 修改与测试、从检查点回滚。打开后进入 Code 新会话，界面显示实际路径和「浏览文件」入口；验证通过后进入本地结果审阅，不创建分支、提交或 PR。每个检查点保存可展开查看的修改差异，重启后仍可审阅；本地任务验证通过后继续提问，会在同一目录创建新任务，保留旧任务的检查点。

文件操作始终限定在选中的目录。选中 Git 仓库的子目录不会自动扩大到父仓库；只有直接打开已有初始提交的 Git 根目录才启用分支、提交与发布功能。尚无提交的 Git 目录先按普通文件夹使用，建立初始提交后重新打开即可启用 Git 功能。

## 必跑验证清单

一个 Code 任务可以有最多 12 条「必跑检查」，它们定义任务必须通过的验证。清单不是建议：**只有所有检查都跑在当前代码上并通过，任务才能提交或进入交付**；配置了完成条件时，还必须逐条提交证据并显式确认。

Code 聊天首次发送后先打开任务准备对话框：确认执行模式、查看或编辑必跑检查，保存后点击「开始任务」才启动后台循环。未保存的修改会阻止启动；关闭对话框后可从该任务的准备入口重新打开，启动失败可以在同一任务上重试。保守模式停在首次计划审批时，尚未开始编码迭代，仍可重新准备；运行中的循环和已开始的首轮不开放该入口。

同一任务的准备对话框关闭后再打开会保留未保存编辑；打开另一任务的准备对话框会切换编辑实例，因此切换前请保存检查与完成条件。

创建任务时若没显式给 `validation_checks`，服务端按仓库根 `package.json` 的 `scripts` 推导默认清单——`test`、`api:test`（与 `test` 不同时）、`typecheck`/`check`/`lint` 中第一个、`build`。推导出的检查都是 `script` 类型、工作目录 `.`、超时 300 秒，且**只记录脚本名，不记录脚本体**。没有 `package.json` 或没有这些脚本时清单为空，退回原来的自动检测单命令。

每条检查的字段：

| 字段 | 说明 |
|---|---|
| `id` | 任务内唯一，`[A-Za-z0-9_-]+`，最长 80 |
| `label` | 界面上显示的名字 |
| `kind` | `auto` / `pytest` / `frontend` / `static` / `script` |
| `workdir` | 相对仓库的目录，必须已存在且不是 `.git` 等受保护路径 |
| `target` | 可选，测试文件或 `文件::用例名`，必须落在 `workdir` 内 |
| `script` | 仅 `script` 类型使用：`package.json` 里声明的脚本名，不能带参数 |
| `timeout_seconds` | 1–300，实际执行时再与 `FORGE_COMMAND_TIMEOUT_SECONDS` 取较小值 |

首轮开始前可以在运行视图编辑清单，或调用 `PUT /api/v1/tasks/{id}/validation-checks`。改清单会改变计划审批绑定的范围，因此需要重新批准计划；首轮开始后清单锁定（`400 INVALID_TASK_STATE`），循环或验证正在运行时返回 `409 WORKSPACE_BUSY`。

`POST /api/v1/runs/{id}/tests` 不带参数时按顺序跑第一条尚未通过的检查，也可以显式传 `check_id` 指定；返回结果带 `check_id`、`workdir`、`target`、`script`，界面据此显示每项状态（`pending` / `passed` / `failed` / `stale` / …）。

失效判定用**整仓输入哈希**：对仓库内所有可读文件的相对路径与内容做 sha256，跳过受保护路径、生成目录与符号链接。因此 `package.json`、依赖、新增测试文件的任何变化都会把已通过的结果标成 `stale`，必须重跑；反过来，只改一个文件也能准确失效，不会因为「改的不是测试文件」而漏掉。

命令构造有两条边界：`script` 类型只允许执行 `package.json` 里声明的脚本名（不能拼参数或命令），包管理器按 `workdir` 向上找到的第一个 lockfile 决定（`pnpm-lock.yaml` / `yarn.lock` / `package-lock.json` / `npm-shrinkwrap.json`，到仓库根为止）；Python 检查优先用 `workdir` 或仓库根的 `.venv`/`venv`，虚拟环境存在但没有解释器时报 `PROJECT_PYTHON_UNAVAILABLE`，不会静默回落到系统 Python。

## 完成条件与证据确认

任务准备区可编辑最多 12 条完成条件，每条文本最多 500 字符。它们与原始目标一同持久化并参与计划审批范围；未保存或保存中不能开始任务，首次迭代后条件锁定。留空兼容原有任务流程。

验证通过后，在运行视图逐条填写证据（每条最多 1000 字符），由操作者明确点击「确认完成条件」。确认绑定当前范围哈希、整仓验证哈希和迭代 ID；代码、范围或迭代变化后旧确认失效，需复核并重新确认，提交与发布同时受此门禁约束。测试通过不会自动认定需求完成，文字证据也不由服务端自动判真。

- `PUT /api/v1/tasks/{id}/completion-criteria`：首轮前保存 `{ criteria: [{ id, text }] }`。
- `POST /api/v1/tasks/{id}/completion-confirmation`：显式提交 `{ scope_hash, validation_hash, iteration_id, evidence: [{ criterion_id, evidence }] }`。
- `GET /api/v1/tasks/{id}/agent` 返回 `completion_status`（`not_required` / `pending` / `stale` / `confirmed`）、当前哈希与 `can_confirm_completion`；`review_completion` 引导进入验证区复核。

## Git 演示流程

```bash
pnpm demo:init                        # 默认生成 .forge/demo/phase-1
python scripts/create_demo.py --name my-demo   # 需要其它名字时
```

该仓库自带一个真实缺陷：`clamp()` 未处理下边界，`test_calculator.py::test_clamp_below_minimum` 会失败。

在 Desktop 中按顺序执行：

1. 「打开文件夹」→ 填入 `.forge/demo/phase-1` 的绝对路径。
2. 「仓库」视图确认文件树、搜索、符号列表和仓库摘要（语言、入口、测试目录、配置文件、包管理器）。
3. 输入任务目标并运行 → 检查三步计划 → 请求并批准方案审批。
4. 创建任务分支（工作区必须干净）。
5. 开始编码轮次 → 在「仓库」视图选中文件编辑，或在 Patch 面板填写路径与内容 → 预览 diff → 确认应用（自动创建检查点）。
6. 运行测试：项目测试 / Python 测试 / 前端测试 / 静态检查，可指定单个测试文件或用例。
7. 失败时查看失败定位，点击「修复」进入下一轮；「执行记录」面板可回滚到任意检查点。
8. 测试通过后预览提交 → 请求并批准写入审批 → 创建任务提交。
9. 重启 API 进程后重新打开该任务：仓库、轮次、审批、测试日志与检查点都还在，任务会提示「需要确认继续」，确认后才允许继续写入。
10. 「受控发布」面板：请求并批准推送审批 → 推送任务分支 → 预览 Draft PR → 请求并批准 PR 审批 → 创建 PR，面板展示 PR 编号、状态与链接，并可「对远端核对」。不接外网时用 `FORGE_REMOTE_PROVIDER=fake` 跑完整流程；接真实 GitHub 时按上面的远程变量配置。

推送、PR 预览与创建共用发布快照校验：当前任务分支和 HEAD 必须匹配已审批提交，任务改动须已提交，创建 PR 还须匹配已推送 SHA。PR 预览展示相对 `origin/<target>` 的三点差异（缺少远端引用时使用本地目标分支），包括分支上此前的 seed 文件；目标引用缺失时提示完整范围不可用并禁止就绪。Git 命令失败时，错误详情包含工作目录、该目录是否存在、`.git` 是否存在、`GIT_*` 环境变量名与实际尝试次数。Windows 上扫描器短暂占用 `.git` 会让 git 报 `not a git repository`，因此这类错误在 `.git` 确实存在时只对只读命令自动重试一次；`commit`、`push` 等会改变仓库状态的命令永不重试，避免重复生效。

命令行等价流程见 `docs/phase-1-todolist.md` 的「推荐接口顺序」。

## 本地 Agent 编排

运行视图的「Agent 下一步」面板根据任务、当前轮次、审批与验证结果决定下一步。点击「推进下一步」每次执行一个动作：发起计划审批、开始编码轮次、运行验证或根据真实测试失败开始修复轮次。计划审批、任务分支、Patch 预览/应用和受控发布继续使用现有面板。

- `GET /api/v1/tasks/{task_id}/agent`：查询 `action`、`can_advance`、`message`，以及当前任务、轮次、待审批记录和最近测试结果。
- `POST /api/v1/tasks/{task_id}/agent/advance`：以 `{}` 推进当前可执行动作；也可指定 `action` 为 `request_plan_approval`、`start_iteration`、`start_repair` 或 `run_tests`。动作已经变化时返回 `409 AGENT_ACTION_NOT_AVAILABLE`，应先刷新状态。
- 测试参数复用原接口：`kind`、`target`、`timeout_seconds`，以及清单用的 `check_id`、`workdir`、`script`；可传 `goal` 指定编码轮次目标、`feedback` 指定修复说明。未传修复说明时使用原任务目标和最近失败信息。
- 推进接口返回新的 Agent 状态与 `performed_action`。等待人工操作、运行中的测试、暂停、取消和重启待恢复状态下，自动推进返回 `noop`。
- 支持 `Idempotency-Key`：重试同一次请求时保持同一键和请求体，下一次推进使用新键；成功响应以 HTTP 200 回放。每次实际推进记录 `agent.advance` 审计事件。

典型顺序为：请求计划审批 → 人工批准并创建任务分支 → 开始轮次 → 人工准备、预览并应用 Patch → 运行验证 → 失败时开始修复轮次，或全部必跑检查通过后进入提交审阅。配置了清单时，单跑一条检查不能推进任务，只有清单全绿才算验证通过；文件变更导致验证过期时会要求重新测试。命令不可用、环境阻塞和超时会交给人工处理。

PR 刷新会识别明确的合并结果并完成任务；关闭但未确认合并的 PR 留待人工复核。

Code 聊天还可启动后台循环（`POST /api/v1/runs/{run_id}/loop`），停止接口为同一路径的 `/stop`。计划仍由规则生成；模型起草保留原始任务目标，追加轮次目标和反馈，并带入仓库根 `AGENTS.md`、显式文件路径、最近失败文件、已改文件，以及有限搜索和入口/测试文件的完整内容。规则文件与源码共用文件数和字节预算；修复轮次保留最近失败测试的摘要与输出。`patch.draft` 审计记录实际上下文路径、UTF-8 字节数、耗时、请求模型及修改说明。

首次 AI 起草前运行修改前验证，基线单独保存，不计作修复失败或任务完成。已有断言失败可以进入修复；测试收集、配置、命令环境和超时阻塞时先转人工处理。没有检测到验证命令的空项目可以先创建代码和测试，改动后仍必须验证。自动检测出的验证器会固定下来，新增其他语言的测试不会替换原验证命令。结果中的 `baseline_comparison` 区分原有、新增和已解决的失败，只比较同一种验证器；人工批准验收变更后清除旧比较基线。

已有测试、验证配置和所选脚本及其包装文件受保护；新增可能改变验收方式的配置也需要审阅。预览返回 `requires_validation_confirmation` 与 `validation_changes`，应用必须另传 `confirm_validation_changes: true`，普通 `confirm: true` 不代表批准验收变更。自动循环遇到这类补丁会在写盘前停止，可通过手动起草取回当前候选进行确认；预览在服务重启后过期，需要重新生成。无效验收变更应用失败会恢复本次检查点；回滚只恢复对应文件的保护记录，其他外部验收修改继续受门禁约束。

草拟携带最近三轮尝试及失败证据。可恢复的格式、缺失上下文、重复路径和无效操作错误会在原有轮数上限内反馈给模型；上下文没有变化且回复重复时提前返回 `AGENT_NO_PROGRESS`。相同代码与失败连续出现也会转人工复核。外部文件冲突、保护路径、二进制和超大修改直接停止。

执行期间，同目录及父子目录互斥，独立目录可并行。后台循环、手动起草和验证会持续占用目录；补丁预览/应用、回滚、分支和提交等入口遇到占用会返回 `WORKSPACE_BUSY`。暂停或取消 Code 任务也会停止等待中的模型起草和命令，迟到回复不会继续应用补丁；占用在执行退出后释放。

- **保守**：人工批准计划后，在每轮补丁前停下；通过「审阅补丁」进入运行面板，AI 起草或手填补丁、预览 diff、确认应用。Git 提交、推送和 PR 也需人工批准。
- **标准**：自动完成计划、补丁、测试及 Git 提交；推送与 PR 等待人工批准。普通文件夹在本地验证通过后停止，保留审阅和回滚入口。
- **自治**：在已启用 Git 的目录中，还可自动推送和创建 Draft PR；普通文件夹仍只执行本地流程。

模型输出统一经过补丁保护路径、大小和检查点门禁。模型只能修改或删除已提供完整上下文的文件，服务端绑定读取时的内容哈希；等待模型期间的外部修改会触发冲突，保留用户的新内容。停止会取消正在等待的模型请求，并在写盘前再次检查停止标记。Code 的模型起草、模型命令、GitHub 校验/推送/PR/刷新共享任务调用预算；成功模型或远程调用不会清除连续失败记录，只有测试通过才清零。

Cowork 使用每次请求的超时和上下文字节上限，长期会话不受 Code 累计预算封锁。可附带目录摘要及有限源码作为只读上下文，不执行文件修改或命令；发给模型的历史最多保留最近 40 条，并遵守上下文总字节上限，原始聊天记录仍完整保存。等待回复时可以停止，超时或取消后重新发送同一文本会复用原请求 ID；成功回复重试直接回放。助手正文支持基础 Markdown、表格和代码复制。聊天输入及附件按会话隔离，切换会话不会把迟到结果写进其他输入框。

模型每轮只回一个 JSON 对象，二选一：请求上下文，或提交改动。请求上下文用 `context_requests`（每次最多 8 条 `search`、`read` 或 `run`），服务端执行后把命中的路径与文件并入下一轮的目标和上下文，最多 4 轮；仍然只提要求就返回 `MODEL_CONTEXT_LIMIT`，不会无限循环。改文件优先用 `edits`（`old_text` / `new_text`，每次替换必须在当前内容里唯一匹配，累计最多 100 处），也可以用 `content` 整文件重写；`create` / `delete` 不接受 `edits`。越界路径（`..`、绝对路径、盘符、`\0`）直接拒绝，未完整读过的已有文件不允许修改（`MODEL_CONTEXT_REQUIRED`）。每次检索与读取都记 `context.request` 审计事件，并计入任务调用预算。`run` 只能执行一个白名单程序（`python`/`pytest`/`node`/`npm`/`pnpm`/`yarn`/`go`/`cargo`/`ruff`/`mypy`/`tsc`/`eslint`），无 shell、工作目录为仓库根、超时受 `FORGE_COMMAND_TIMEOUT_SECONDS` 约束，子进程环境会剥离 `FORGE_*` 与凭据类变量；保守模式直接拒绝。白名单里包含 `npm`/`pnpm`/`yarn`，因此模型仍可能通过包脚本执行仓库里定义的任意代码——这是本地 Agent 的已知边界，不是沙箱。模型自己跑的命令不算验证证据，必跑检查仍会在改动后执行；真正执行的命令按次计入任务调用预算，被白名单挡下的不计。模型返回时若任务已被暂停或取消，这份回复会被丢弃，不落补丁。

## 聊天结果与上下文

Code 聊天正文末尾提供执行摘要，可查看当前轮次的改动文件、必跑检查、实际命令和完成条件进度，并进入文件浏览、执行记录或完成复核。过期检查不会计为通过，回滚后的改动不计为当前改动；修改前验证单独标记，不能代表改动后的验证完成。工具事件可展开查看修改行数、检查点、检查结果和失败文件行号。

助手正文支持标题、列表、引用、任务列表、表格、行内代码与代码围栏。表格保留转义分隔符和行内代码中的竖线；代码复制保留原始缩进，并提示复制成功或失败。长代码局部滚动并支持键盘聚焦，原始 HTML 保持文本，链接仅开放安全协议。

上下文菜单区分当前待发送输入/附件和上次实际请求，展示附件大小、请求模型与 Provider、读取时间、来源路径及上下文字节数。Code 预算展示轮次、耗时、调用次数和连续失败，并说明耗尽原因与下一步；字符数、字节数和调用预算不换算成 token 使用率。停止请求显示等待退出的状态，失败可重试；聊天记录加载或轮询失败时可刷新。

## 验证

```bash
pnpm api:test     # API 单元 + 集成测试（会创建真实 Git 仓库夹具；远程段用 fake Provider，不出网）
pnpm api:init     # 创建/查看本地状态存储，并打印已保存的记录数
pnpm check        # Desktop 类型检查
pnpm build        # Desktop 生产构建
pnpm desktop:smoke # 校验构建产物完整性与关键样式/DOM 字符串（不是交互回放）
pnpm desktop:chat-check # 真实组件渲染回归：文件夹、错误、审阅、发布入口与验证清单编辑/锁定（不是浏览器点击）
pnpm desktop:session-check # 编译实际 App/API 回调，检查会话归属、取消、重试及端口切换
pnpm desktop:runtime-check # 托管进程生命周期、崩溃重试和桌面桥状态检查
pnpm desktop:run-summary-check # 执行摘要：检查过期、回滚、失败定位和完成证据状态
pnpm desktop:markdown-check # Markdown 子集、安全链接、原始代码复制与反馈
pnpm desktop:event-details-check # 工具事件元数据、修改前验证和文件定位回调
pnpm desktop:presentation-check # 上下文/附件、预算、停止反馈、刷新和摘要集成
```

新增展示检查使用实际组件渲染和受控回调夹具，不连接真实模型或远端，也不代表真实窗口点击与视觉验收。

## 故障排查

- **`GIT_COMMAND_FAILED`**：确认 `git` 在 PATH 中，且 API 进程能进入目标仓库目录。
- **`GIT_UNAVAILABLE`（HTTP 503）**：API 进程连 `git` 子进程都拉不起来（Windows 上常见退出码 `3221225794` = `STATUS_DLL_INIT_FAILED`）。这属于该进程自身的启动上下文问题，从普通终端重启 API 即可；子进程环境块已由 `process_env.child_env()` 统一补全并清洗 `GIT_*`，因此不再依赖启动时的临时目录与 git 配置。
- **界面显示 0 仓库 0 任务但 API 有数据**：多为 CORS 或 API 地址不匹配，检查 `FORGE_CORS_ORIGINS` 与 `VITE_API_BASE_URL`。
- **`WORKTREE_NOT_CLEAN`**：任务分支必须在干净工作区上创建，先提交或还原本地改动。
- **`WORKSPACE_BUSY`**：该目录或其父子目录仍有执行中的任务、起草或验证。先停止执行并等待退出，再修改、回滚或启动其他任务。
- **`VALIDATION_BASELINE_BLOCKED`**：修改前验证不可执行或环境阻塞。先解决报告中的命令、依赖或配置问题，再重试起草。
- **`VALIDATION_REVIEW_REQUIRED`**：验收文件或脚本发生变化。预览受影响文件并单独确认；补丁之外的外部验收变更不能顺带批准。
- **`AGENT_NO_PROGRESS`**：相同上下文中重复无效请求或补丁。补充定位证据或修改反馈后重试，也可恢复检查点。
- **`COMMIT_PREVIEW_STALE` / `TEST_RESULTS_STALE`**：审批绑定的是具体的 diff 与验证结果；改动文件或消息后需重新预览、重新审批。
- **`COMPLETION_REQUIRED` / `COMPLETION_CONFIRMATION_STALE`**：完成条件尚未确认，或确认绑定的范围、迭代、验证已改变。通过当前必跑检查后逐条复核证据，并明确重新确认。
- **桌面壳启动即退出，报 `Cannot read properties of undefined (reading 'setName')`**：`ELECTRON_RUN_AS_NODE=1` 被父进程继承了（IDE、VS Code、WorkBuddy 这类 Electron 宿主都会设），`electron.exe` 于是按纯 Node 运行 `main.cjs`，`require("electron").app` 为 undefined。`scripts/electron.js` 启动时已剥离该变量；若绕过启动器直接跑 `electron .`，自己 `unset` 掉。
- **`APPROVAL_REQUIRED`**：首次编码轮次需要方案审批，创建提交需要写入审批。
- **`RECOVERY_CONFIRMATION_REQUIRED`**：API 在该任务执行期间重启过。先在桌面端「确认继续」，或调用 `POST /api/v1/tasks/{id}/recover`；回滚、取消与查询不受此限制。
- **`REMOTE_NOT_CONFIGURED`**：目标仓库没有配置该 remote（默认 `origin`）。API 不会猜测远程地址，先 `git remote add origin <url>`。
- **`DISALLOWED_REMOTE_HOST` / `DISALLOWED_REPOSITORY`**：远程地址不在 `FORGE_ALLOWED_REMOTE_HOSTS` 或 `FORGE_ALLOWED_REPOSITORIES` 白名单内。
- **`AUTHENTICATION_FAILED`（401）/ `RATE_LIMITED_OR_FORBIDDEN`（429）/ `REMOTE_NETWORK_ERROR`、`PUSH_REJECTED`、`PR_CREATION_FAILED`（502）/ `REMOTE_TIMEOUT`（504）**：远程段的状态码映射。推送用 `git` 自身的凭据助手，若提示需要交互输入凭据会立即失败（`GIT_TERMINAL_PROMPT=0`）；REST 用 `FORGE_GITHUB_TOKEN`，令牌不会写入库、审计或错误信息。
- **`NO_APPROVED_COMMIT` / `REMOTE_HEAD_MOVED` / `TASK_BRANCH_NOT_CHECKED_OUT`（409）**：推送绑定的是「已审批的那个提交」。工作区切走了分支或 HEAD 变了，就重新预览并提交，绝不会把别人的提交推上去。
- **`BRANCH_NOT_PUSHED`（409）/ `REMOTE_SOURCE_BRANCH_MISSING` / `REMOTE_TARGET_BRANCH_MISSING`**：创建 PR 前必须已推送任务分支，且源/目标分支在远端仍然存在。
- **`IDEMPOTENCY_KEY_REUSED`（409）**：同一个 `Idempotency-Key` 被用于不同端点或不同请求体。换一个键；同键同体重发会直接返回首次结果，不会二次推送或二次建 PR。
- **`AGENT_ACTION_NOT_AVAILABLE`（409）**：请求的 Agent 动作与当前任务下一步不匹配，刷新 Agent 状态后继续。
- **`IDEMPOTENCY_IN_PROGRESS`（409）**：同键的另一个请求仍在执行中。
- **`PATCH_TOO_LARGE` / `PROTECTED_PATH` / `BINARY_FILE_NOT_ALLOWED`**：受 Phase 1 安全限制约束，缩小改动范围或排除受保护路径（`.env`、私钥、凭据文件、`.git`、依赖与构建目录）。

## 当前状态与已知限制

Phase 1（单仓库理解 → 计划 → 低风险修改 → 测试 → 审计 → 回滚）已实现，保留此前浏览器与 API 验证记录；本轮完整浏览器点击验收仍未完成。
Phase 2 的本地部分与远程代码路径均已完成：多轮编码、验证循环、审批门禁、任务分支、提交、推送、GitHub Provider 与 Draft PR、幂等键、持久化与恢复。

- 状态保存在本地 sqlite（默认 `.forge/state`，含检查点文件与提交/远程分支/PR 记录）；API 重启后未完成任务必须显式确认才继续写入。普通审计与测试输出有保留期限与上限（`FORGE_AUDIT_*` / `FORGE_TEST_OUTPUT_*`）；聊天记录保留到对应会话或文件夹会话被删除。
- 真实 GitHub 验收已在 `sarffff/ai-agent` 完成独立夹具修复、完成确认、精确审批提交、任务分支推送与 [Draft PR #1](https://github.com/sarffff/ai-agent/pull/1) 创建/刷新；GitHub 独立核对 head、main 基点和两个夹具文件。离线回归仍使用 fake Provider，主应用和远端 main 未修改。
- 幂等键覆盖 Agent 推进、推送和创建 PR；提交仍需审批与预览确认。
- 任务预算熔断已接入：最大轮次、总耗时、远程调用次数、连续失败阈值（`FORGE_TASK_MAX_*`），任一触顶即锁定并转人工复核。
- 本轮单文件验收任务在测试通过、重新确认后因等待超过一小时耗尽 wall-time 预算；已有证据保留，但复跑需新建任务，不能把历史 `confirmed` 当成仍可继续执行的状态。
- 任务可配置必跑验证清单（默认按 `package.json` 推导），清单全绿才允许提交；验证结果绑定整仓输入哈希，任何文件变化都会把它标成过期。首轮开始后清单锁定。
- 完成条件与逐条证据确认已接入任务准备、运行视图和提交/发布门禁；单文件、两文件与故障注入修复的真实模型场景均已通过原测试并由操作者显式确认。
- 已有本地 Agent 单步编排和后台循环；计划由规则/模板生成，模型通过 Anthropic 或 OpenAI 兼容接口起草，支持 `edits` 精确替换与 `context_requests` 自查上下文。实际写入权限取决于上述模式。无沙箱容器，命令在主机上以受控白名单方式执行。
- 起草上下文优先显式路径和失败定位，找到目标后只补有限关联文件；真实 GitHub 夹具的上下文由 119930 缩至 452 字节。单行 `edits` 引导使用不带换行的最小唯一匹配，多行替换保留 CRLF；复杂调用关系选择和跨轮证据压缩仍待完善。
- 审批中心的拒绝、要求修改、过期列表与预算视图已接入。产物冒烟与组件渲染检查不能替代浏览器交互；本轮 `127.0.0.1:15173` 页面保存被权限/managed policy 拒绝，完整点击回放仍待完成。
- 桌面壳（Electron）已接入并与浏览器路径并存：壳托管本地 API、状态落在 `userData`、原生目录选择器。窗口层已真机验收（`pnpm desktop:electron:smoke` 14 条断言全过，见上面的桌面壳小节）；没有打包分发配置，`apps/api` 仍以源码方式运行。
- 单文件夹任务；仓库可在磁盘任意位置，`FORGE_WORKSPACE_ROOT` 只提供默认起点；Git 子目录保留所选范围并使用本地流程。多仓库、CI 交互、Review 评论、冲突处理与自动合并属于后续阶段。

当前核心状态以 [2026-10-24 验收报告](docs/core-agent-acceptance-20261024.md) 和 `docs/todo.md` 为准；阶段清单保留历史口径。Phase 3（CI 交互、自动修复、冲突处理与治理）的范围见 `docs/phase-3-todolist.md`。
