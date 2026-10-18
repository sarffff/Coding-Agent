# Coding Agent (Forge)

本地 Coding Agent 的 Monorepo：以文件夹为入口，支持可审计、可恢复的修改与验证，Git 发布为可选能力。

## 目录

```text
apps/
  desktop/   React + Vite + TypeScript 工作台
    electron/  桌面壳（main / backend 生命周期 / preload / config），可选
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
- Python 3.11+（开发与验证使用 3.14）
- 可用的 `git` 命令行

## 安装与启动

```bash
pnpm install
python -m venv .venv && .venv/Scripts/pip install -r apps/api/requirements-dev.txt   # Windows
pnpm api:test        # 脚本会自动优先使用 .venv 中的解释器

pnpm start          # 同时启动 API 与 Desktop（run.js）
pnpm api:dev        # 只启动 API：http://127.0.0.1:8000
pnpm dev            # 只启动 Desktop：http://127.0.0.1:5173
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
| `FORGE_LLM_PROVIDER` | 同上 | 补丁起草 Provider：`anthropic` / `openai` / 离线 `fake`（仅测试，需显式 seed） | `anthropic` |
| `FORGE_ANTHROPIC_API_KEY` / `FORGE_OPENAI_API_KEY` | 同上 | 对应 Provider 的 API key，用于补丁起草；只进请求头 | 空 |
| `FORGE_LLM_MODEL` | 同上 | 起草模型，须与 provider 匹配（anthropic 可设 `claude-opus-5-5`，openai 设 gpt 模型；其余 `FORGE_LLM_*`/`FORGE_OPENAI_API_URL` 见 `.env.example`） | `claude-sonnet-5-5` |
| `FORGE_CORS_ORIGINS` | 同上 | 允许访问 API 的源 | `http://localhost:5173`、`http://127.0.0.1:5173` |
| `VITE_API_BASE_URL` | `apps/desktop/.env` | Desktop 连接的 API 地址 | `http://127.0.0.1:8000` |

复制 `apps/api/.env.example` 与 `apps/desktop/.env.example` 为 `.env` 后再修改。

> CORS 默认只放行 5173。Desktop 运行在其他端口时必须把该端口加入 `FORGE_CORS_ORIGINS`，否则请求会被浏览器拦截，界面表现为“没有仓库、没有任务”。桌面壳不需要这一步——它会把自己的来源注入 `FORGE_CORS_ORIGINS`。

## 桌面壳（Electron）

`pnpm desktop:electron` 会构建前端、**自己拉起并托管本地 API**，然后打开原生窗口。与 `pnpm start`（浏览器 + `run.js`）并存，互不影响。

```bash
pnpm desktop:electron        # 构建产物 + 托管 API + 打开窗口
pnpm desktop:electron:dev    # 连同 vite 开发服务器启动（HMR，默认 5173）
pnpm desktop:electron:smoke  # 隐藏窗口交互自检：14 条断言，全过才退出码 0
```

自检覆盖的是**真窗口里才成立**的那些事，而不是静态检查：窗口加载地址与 `location.origin` 都是 `forge-app://desktop`、CSS 子资源也经协议 handler 取到、渲染进程直接 `fetch` 壳托管的 API（这才是那条 CORS 边界的实证）、React 受控输入写进目标框后「发送」由 disabled 变可用、端口记忆已落盘、以及选择器之后的完整链路——侧栏登记文件夹 → 选中 → 文件树 → 读到正文标记串。

自检模式使用**可丢弃的根**（`userData/state-smoke` 与 `~/forge-workspace-acceptance`，每次启动先清空），因为 API 没有注销仓库的接口，测试数据必须与真实桌面状态隔离。原生目录选择器会弹模态框、把自动检查死锁住，所以这三项仍需人工：原生选择器、「换工作区」确认框、深色主题对比度。

壳负责的事情：

- **进程生命周期**：用 `.venv` 里的解释器（可用 `FORGE_PYTHON` 指定）以 `python -m uvicorn app.main:app --app-dir apps/api` 启动，只绑定 `127.0.0.1` 的临时端口；退出窗口时连同子进程一起结束。
- **端口记忆与认领校验**：端口记在 `%LOCALAPPDATA%/Forge Coding Agent/forge-desktop.json`。重启沿用同一端口，因此前端无需重载；但若发现该端口上已有别的进程在应答 `/health`，壳会换端口——**绝不认领不是自己启动的 API**。
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

从侧栏或输入框旁点击「打开本地文件夹」：Electron 直接弹出系统目录选择器，选中后打开并进入 Code 新会话；浏览器显示绝对路径输入框。已打开的文件夹在侧栏独立分组，即使还没有任务也能新建对话；输入框旁的「项目或文件夹」菜单可切换已有目录，并查看完整路径。目录可以是磁盘上任意文件夹（盘符与文件系统根除外），打开失败会保留所选路径，桌面端可展开「工作区设置」更换默认根目录。

普通文件夹不需要 `git init`：可以浏览与搜索文件、运行 Code 的 AI 修改与测试、从检查点回滚。打开后进入 Code 新会话，界面显示实际路径和「浏览文件」入口；验证通过后进入本地结果审阅，不创建分支、提交或 PR。每个检查点保存可展开查看的修改差异，重启后仍可审阅；本地任务验证通过后继续提问，会在同一目录创建新任务，保留旧任务的检查点。

文件操作始终限定在选中的目录。选中 Git 仓库的子目录不会自动扩大到父仓库；只有直接打开已有初始提交的 Git 根目录才启用分支、提交与发布功能。尚无提交的 Git 目录先按普通文件夹使用，建立初始提交后重新打开即可启用 Git 功能。

## 必跑验证清单

一个 Code 任务可以有最多 12 条「必跑检查」，它们共同定义「这个任务算做完了」。清单不是建议：**只有所有检查都跑在当前离线代码上并通过，任务才能提交或进入交付**。

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

Code 聊天还可启动后台循环（`POST /api/v1/runs/{run_id}/loop`），停止接口为同一路径的 `/stop`。计划仍由规则生成；模型起草会带入显式文件路径、最近失败文件、已改文件，以及有限搜索和入口/测试文件的完整内容，受文件数和字节预算约束。修复轮次保留最近失败测试的摘要与输出。

- **保守**：人工批准计划后，在每轮补丁前停下；通过「审阅补丁」进入运行面板，AI 起草或手填补丁、预览 diff、确认应用。Git 提交、推送和 PR 也需人工批准。
- **标准**：自动完成计划、补丁、测试及 Git 提交；推送与 PR 等待人工批准。普通文件夹在本地验证通过后停止，保留审阅和回滚入口。
- **自治**：在已启用 Git 的目录中，还可自动推送和创建 Draft PR；普通文件夹仍只执行本地流程。

模型输出统一经过补丁保护路径、大小和检查点门禁。模型只能修改或删除已提供完整上下文的文件，服务端绑定读取时的内容哈希；等待模型期间的外部修改会触发冲突，保留用户的新内容。停止会取消正在等待的模型请求，并在写盘前再次检查停止标记。模型起草、Cowork 对话、GitHub 校验/推送/PR/刷新共享任务调用预算；成功模型或远程调用不会清除连续失败记录，只有测试通过才清零。Cowork 仍是附带可选目录摘要的聊天，不执行文件修改或命令。

模型每轮只回一个 JSON 对象，二选一：请求上下文，或提交改动。请求上下文用 `context_requests`（每次最多 8 条 `search` 或 `read`），服务端执行后把命中的路径与文件并入下一轮的目标和上下文，最多 4 轮；仍然只提要求就返回 `MODEL_CONTEXT_LIMIT`，不会无限循环。改文件优先用 `edits`（`old_text` / `new_text`，每次替换必须在当前内容里唯一匹配，累计最多 100 处），也可以用 `content` 整文件重写；`create` / `delete` 不接受 `edits`。越界路径（`..`、绝对路径、盘符、`\0`）直接拒绝，未完整读过的已有文件不允许修改（`MODEL_CONTEXT_REQUIRED`）。每次检索与读取都记 `context.request` 审计事件，并计入任务调用预算；模型返回时若任务已被暂停或取消，这份回复会被丢弃，不落补丁。

## 验证

```bash
pnpm api:test     # API 单元 + 集成测试（会创建真实 Git 仓库夹具；远程段用 fake Provider，不出网）
pnpm api:init     # 创建/查看本地状态存储，并打印已保存的记录数
pnpm check        # Desktop 类型检查
pnpm build        # Desktop 生产构建
pnpm desktop:smoke # 校验构建产物完整性与关键样式/DOM 字符串（不是交互回放）
pnpm desktop:chat-check # 真实组件渲染回归：文件夹、错误、审阅、发布入口与验证清单编辑/锁定（不是浏览器点击）
```

## 故障排查

- **`GIT_COMMAND_FAILED`**：确认 `git` 在 PATH 中，且 API 进程能进入目标仓库目录。
- **`GIT_UNAVAILABLE`（HTTP 503）**：API 进程连 `git` 子进程都拉不起来（Windows 上常见退出码 `3221225794` = `STATUS_DLL_INIT_FAILED`）。这属于该进程自身的启动上下文问题，从普通终端重启 API 即可；子进程环境块已由 `process_env.child_env()` 统一补全并清洗 `GIT_*`，因此不再依赖启动时的临时目录与 git 配置。
- **界面显示 0 仓库 0 任务但 API 有数据**：多为 CORS 或 API 地址不匹配，检查 `FORGE_CORS_ORIGINS` 与 `VITE_API_BASE_URL`。
- **`WORKTREE_NOT_CLEAN`**：任务分支必须在干净工作区上创建，先提交或还原本地改动。
- **`COMMIT_PREVIEW_STALE` / `TEST_RESULTS_STALE`**：审批绑定的是具体的 diff 与验证结果；改动文件或消息后需重新预览、重新审批。
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

Phase 1（单仓库理解 → 计划 → 低风险修改 → 测试 → 审计 → 回滚）已完成并在浏览器与 API 两端验证。
Phase 2 的本地部分与远程代码路径均已完成：多轮编码、验证循环、审批门禁、任务分支、提交、推送、GitHub Provider 与 Draft PR、幂等键、持久化与恢复。

- 状态保存在本地 sqlite（默认 `.forge/state`，含检查点文件与提交/远程分支/PR 记录）；API 重启后未完成任务必须显式确认才继续写入。审计与测试输出有保留期限与上限（`FORGE_AUDIT_*` / `FORGE_TEST_OUTPUT_*`）。
- 远程段已接通并带完整门禁，但**尚未对真实 GitHub 验证过**：测试与演示走 `FORGE_REMOTE_PROVIDER=fake`，真实令牌与演示仓库仍缺（见 `docs/todo.md` 的 P1）。
- 幂等键覆盖 Agent 推进、推送和创建 PR；提交仍需审批与预览确认。
- 任务预算熔断已接入：最大轮次、总耗时、远程调用次数、连续失败阈值（`FORGE_TASK_MAX_*`），任一触顶即锁定并转人工复核。
- 任务可配置必跑验证清单（默认按 `package.json` 推导），清单全绿才允许提交；验证结果绑定整仓输入哈希，任何文件变化都会把它标成过期。首轮开始后清单锁定。
- 已有本地 Agent 单步编排和后台循环；计划由规则/模板生成，模型通过 Anthropic 或 OpenAI 兼容接口起草，支持 `edits` 精确替换与 `context_requests` 自查上下文。实际写入权限取决于上述模式。无沙箱容器，命令在主机上以受控白名单方式执行。
- 审批中心的拒绝、要求修改、过期列表与预算视图已接入。产物冒烟与组件渲染检查不能替代浏览器交互；设置等扩展入口仍未完成。
- 桌面壳（Electron）已接入并与浏览器路径并存：壳托管本地 API、状态落在 `userData`、原生目录选择器。窗口层已真机验收（`pnpm desktop:electron:smoke` 14 条断言全过，见上面的桌面壳小节）；没有打包分发配置，`apps/api` 仍以源码方式运行。
- 单文件夹任务；仓库可在磁盘任意位置，`FORGE_WORKSPACE_ROOT` 只提供默认起点；Git 子目录保留所选范围并使用本地流程。多仓库、CI 交互、Review 评论、冲突处理与自动合并属于后续阶段。

各阶段勾选与验收口径以 `docs/phase-1-todolist.md`、`docs/phase-2-todolist.md` 为准；Phase 3（CI 交互、自动修复、冲突处理与治理）的范围见 `docs/phase-3-todolist.md`，其第 0 节列出了开工前必须先收口的 Phase 2 遗留项。
