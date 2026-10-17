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
| `FORGE_WORKSPACE_ROOT` | `apps/api/.env` | API 允许读写的仓库根目录，所有路径都必须落在其内 | 进程启动目录 |
| `FORGE_STATE_DIR` | 同上 | 状态存储目录：`state.db` 与 `checkpoints/` | `.forge/state` |
| `FORGE_MAX_FILE_SIZE_BYTES` | 同上 | 单文件读写上限 | 1000000 |
| `FORGE_MAX_TREE_ENTRIES` / `FORGE_MAX_SEARCH_RESULTS` | 同上 | 树与搜索的返回上限 | 5000 / 200 |
| `FORGE_GIT_TIMEOUT_SECONDS` / `FORGE_COMMAND_TIMEOUT_SECONDS` | 同上 | Git 与测试命令超时 | 10 / 30 |
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
pnpm desktop:electron:smoke  # 隐藏窗口自检：DOM 已挂载、桥可用、/health 通过
```

壳负责的事情：

- **进程生命周期**：用 `.venv` 里的解释器（可用 `FORGE_PYTHON` 指定）以 `python -m uvicorn app.main:app --app-dir apps/api` 启动，只绑定 `127.0.0.1` 的临时端口；退出窗口时连同子进程一起结束。
- **端口记忆与认领校验**：端口记在 `%LOCALAPPDATA%/Forge Coding Agent/forge-desktop.json`。重启沿用同一端口，因此前端无需重载；但若发现该端口上已有别的进程在应答 `/health`，壳会换端口——**绝不认领不是自己启动的 API**。
- **来源与凭据边界**：窗口通过特权自定义协议 `forge-app://desktop` 加载产物（`file://` 的 Origin 是 `null`，CORS 无法放行），并把 `forge-app://desktop` 注入 `FORGE_CORS_ORIGINS`。渲染进程 `contextIsolation + sandbox + 无 nodeIntegration`，只暴露 `window.forge` 的少量方法；外链交给系统浏览器，禁止 `<webview>`。
- **状态位置**：`state.db` 与检查点落在 `userData/state`，后端日志在 `userData/logs/backend.log`（面板里可一键打开），不再依赖启动上下文的工作目录。
- **工作区根目录**：API 只允许读写 `FORGE_WORKSPACE_ROOT` 内的仓库，壳用原生目录选择器维护它（默认 `~/forge-workspace`），更换会重启后端且保持端口不变。

| 变量 | 作用 |
|---|---|
| `FORGE_PYTHON` | 指定后端解释器，优先于仓库 `.venv` |
| `FORGE_ELECTRON_DEV_PORT` | `desktop:electron:dev` 使用的 vite 端口（默认 5173） |
| `FORGE_API_ROOT` | 后端源码所在的仓库根目录；源码运行时自动推导，打包版必须显式提供 |
| `FORGE_ELECTRON_SMOKE` / `FORGE_ELECTRON_DEV_URL` | 自检模式 / 开发模式加载地址（由脚本设置） |

壳里不依赖窗口的两层都有真机进程验证，可在 ACL 受限的环境外跑：

```bash
pnpm desktop:backend-test # 真的拉起 uvicorn 子进程：根目录注入、CORS 放行、端口记忆与停手后释放、失败分类
pnpm desktop:unit         # 选择器与协议路径解析的纯逻辑：长路径前缀、建议起点、dist 穿越防护
```

`desktop:backend-test` 会用临时目录当 `FORGE_WORKSPACE_ROOT`/`FORGE_STATE_DIR` 起一个真实的 API 子进程，确认壳注入的环境变量确实压过 `apps/api/.env`，并验证「 remembered 端口上已有别人在应答 `/health` 就换端口」这条认领边界。启动失败分三类报告：解释器不存在（`无法启动本地 API`）、进程自己退了（`本地 API 提前退出`，附日志尾部）、30 秒没就绪。

> **Windows 上的一个已知前置条件**：Electron 启动时会检查 `node_modules/.../electron/dist` 是否允许 AppContainer 读取。若工作区处于受限账户 ACL 之下（本仓开发环境就是这样），会看到
> `FATAL:install_dir_access.cc ... none for ALL APPLICATION PACKAGES`，此时任何 Electron 应用（包括空应用）都起不来，与本项目代码无关。在**你自己的终端**里执行一次即可：
> `icacls "node_modules\.pnpm\electron@<版本>\node_modules\electron\dist" /grant *S-1-15-2-1:(OI)(CI)(RX)`
> 或者把仓库放在正常 ACL 路径下。当前分支的桌面壳就是在受限工作区里完成实现的，窗口层尚未真机验证。
>
> 关于这道检查，实测过两件事，别再花时间试第三条路：
> - **`--no-sandbox` 在 Electron 44 上无效**。这个 FATAL 来自 Electron 自己的 `install_dir_access.cc`，与渲染进程是否沙箱化无关（`--no-sandbox`、`--disable-gpu`、`ELECTRON_DISABLE_SANDBOX=1` 全部实测照旧）。
> - **降级到没有这道检查的旧版（如 31.x）确实能开窗口**，但要同时带 `--no-sandbox`——旧版没检查，沙箱子进程照样读不到 `dist`，只是换成 GPU 进程 `0xC0000135`（STATUS_DLL_NOT_FOUND）后 FATAL。也就是说代价是**放弃渲染进程沙箱**加上一个落后的 Chromium，不是免费的绕行。补那条 ACE 才是恢复配置，而不是削弱它。

## 打开文件夹

从侧栏或输入框旁点击「打开本地文件夹」：Electron 直接弹出系统目录选择器，选中后打开并进入 Code 新会话；浏览器显示绝对路径输入框。已打开的文件夹在侧栏独立分组，即使还没有任务也能新建对话；输入框旁的「项目或文件夹」菜单可切换已有目录，并查看完整路径。目录需位于配置的 `FORGE_WORKSPACE_ROOT` 内，打开失败会保留所选路径，桌面端可展开「工作区设置」更换根目录。

普通文件夹不需要 `git init`：可以浏览与搜索文件、运行 Code 的 AI 修改与测试、从检查点回滚。打开后进入 Code 新会话，界面显示实际路径和「浏览文件」入口；验证通过后进入本地结果审阅，不创建分支、提交或 PR。每个检查点保存可展开查看的修改差异，重启后仍可审阅；本地任务验证通过后继续提问，会在同一目录创建新任务，保留旧任务的检查点。

文件操作始终限定在选中的目录。选中 Git 仓库的子目录不会自动扩大到父仓库；只有直接打开已有初始提交的 Git 根目录才启用分支、提交与发布功能。尚无提交的 Git 目录先按普通文件夹使用，建立初始提交后重新打开即可启用 Git 功能。

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
- 测试参数复用原接口：`kind`、`target`、`timeout_seconds`；可传 `goal` 指定编码轮次目标、`feedback` 指定修复说明。未传修复说明时使用原任务目标和最近失败信息。
- 推进接口返回新的 Agent 状态与 `performed_action`。等待人工操作、运行中的测试、暂停、取消和重启待恢复状态下，自动推进返回 `noop`。
- 支持 `Idempotency-Key`：重试同一次请求时保持同一键和请求体，下一次推进使用新键；成功响应以 HTTP 200 回放。每次实际推进记录 `agent.advance` 审计事件。

典型顺序为：请求计划审批 → 人工批准并创建任务分支 → 开始轮次 → 人工准备、预览并应用 Patch → 运行测试 → 失败时开始修复轮次，或通过后进入提交审阅。静态检查单独通过不能满足提交门禁；文件变更导致验证过期时会要求重新测试。命令不可用、环境阻塞和超时会交给人工处理。

PR 刷新会识别明确的合并结果并完成任务；关闭但未确认合并的 PR 留待人工复核。

Code 聊天还可启动后台循环（`POST /api/v1/runs/{run_id}/loop`），停止接口为同一路径的 `/stop`。计划仍由规则生成；模型起草会带入显式文件路径、最近失败文件、已改文件，以及有限搜索和入口/测试文件的完整内容，受文件数和字节预算约束。修复轮次保留最近失败测试的摘要与输出。

- **保守**：人工批准计划后，在每轮补丁前停下；通过「审阅补丁」进入运行面板，AI 起草或手填补丁、预览 diff、确认应用。Git 提交、推送和 PR 也需人工批准。
- **标准**：自动完成计划、补丁、测试及 Git 提交；推送与 PR 等待人工批准。普通文件夹在本地验证通过后停止，保留审阅和回滚入口。
- **自治**：在已启用 Git 的目录中，还可自动推送和创建 Draft PR；普通文件夹仍只执行本地流程。

模型输出统一经过补丁保护路径、大小和检查点门禁。模型只能修改或删除已提供完整上下文的文件，服务端绑定读取时的内容哈希；等待模型期间的外部修改会触发冲突，保留用户的新内容。停止会取消正在等待的模型请求，并在写盘前再次检查停止标记。模型起草、Cowork 对话、GitHub 校验/推送/PR/刷新共享任务调用预算；成功模型或远程调用不会清除连续失败记录，只有测试通过才清零。Cowork 仍是附带可选目录摘要的聊天，不执行文件修改或命令。

## 验证

```bash
pnpm api:test     # API 单元 + 集成测试（会创建真实 Git 仓库夹具；远程段用 fake Provider，不出网）
pnpm api:init     # 创建/查看本地状态存储，并打印已保存的记录数
pnpm check        # Desktop 类型检查
pnpm build        # Desktop 生产构建
pnpm desktop:smoke # 校验构建产物完整性与关键样式/DOM 字符串（不是交互回放）
pnpm desktop:unit  # 桌面壳纯逻辑单测：Windows 长路径前缀、选择器属性、建议起点、协议路径穿越防护
pnpm desktop:backend-test # 桌面壳托管 API 的生命周期回归（需要仓库 .venv，会真的起一个 uvicorn 子进程）
pnpm desktop:chat-check # 真实组件渲染回归：文件夹、错误、审阅与发布入口（不是浏览器点击）
```

## 故障排查

- **`GIT_COMMAND_FAILED`**：确认 `git` 在 PATH 中，且 API 进程能进入目标仓库目录。
- **`GIT_UNAVAILABLE`（HTTP 503）**：API 进程连 `git` 子进程都拉不起来（Windows 上常见退出码 `3221225794` = `STATUS_DLL_INIT_FAILED`）。这属于该进程自身的启动上下文问题，从普通终端重启 API 即可；子进程环境块已由 `process_env.child_env()` 统一补全并清洗 `GIT_*`，因此不再依赖启动时的临时目录与 git 配置。
- **界面显示 0 仓库 0 任务但 API 有数据**：多为 CORS 或 API 地址不匹配，检查 `FORGE_CORS_ORIGINS` 与 `VITE_API_BASE_URL`。
- **已登记的仓库全部报 `PATH_OUTSIDE_WORKSPACE`**：`pnpm start`/`pnpm api:dev` 读取 `apps/api/.env`，其中 `FORGE_WORKSPACE_ROOT` 决定哪些路径算“在工作区内”。这个值指向一个不存在或已挪走的目录时，仓库列表仍会显示（`list()` 不复查），但打开文件、Git、发布都会逐个被 `get()` 的归属复查拒掉。改回包含这些仓库的根目录，或临时用环境变量覆盖：`FORGE_WORKSPACE_ROOT=<仓库根> node run.js`（真实环境变量优先于 `.env`）。桌面壳不受影响——它总是自己注入这个值。
- **`WORKTREE_NOT_CLEAN`**：任务分支必须在干净工作区上创建，先提交或还原本地改动。
- **`COMMIT_PREVIEW_STALE` / `TEST_RESULTS_STALE`**：审批绑定的是具体的 diff 与验证结果；改动文件或消息后需重新预览、重新审批。
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
- **`PATH_OUTSIDE_WORKSPACE`**：要打开的文件夹不在 `FORGE_WORKSPACE_ROOT` 内。桌面壳会就地给出"把工作区切到它的上级"的出路；注意更换根目录会重启 API，且**根目录之外的已登记仓库会被暂时锁定**（`get()` 会重新确认归属），切回原根目录即恢复。
- **`PATH_IS_FILESYSTEM_ROOT`**：原生目录选择器能选到 `D:\` 这类盘符根，而它不能当仓库——否则文件树与补丁的范围就是整块盘。
- **`PATCH_TOO_LARGE` / `PROTECTED_PATH` / `BINARY_FILE_NOT_ALLOWED`**：受 Phase 1 安全限制约束，缩小改动范围或排除受保护路径（`.env`、私钥、凭据文件、`.git`、依赖与构建目录）。

## 当前状态与已知限制

Phase 1（单仓库理解 → 计划 → 低风险修改 → 测试 → 审计 → 回滚）已完成并在浏览器与 API 两端验证。
Phase 2 的本地部分与远程代码路径均已完成：多轮编码、验证循环、审批门禁、任务分支、提交、推送、GitHub Provider 与 Draft PR、幂等键、持久化与恢复。

- 状态保存在本地 sqlite（默认 `.forge/state`，含检查点文件与提交/远程分支/PR 记录）；API 重启后未完成任务必须显式确认才继续写入。审计与测试输出有保留期限与上限（`FORGE_AUDIT_*` / `FORGE_TEST_OUTPUT_*`）。
- 远程段已接通并带完整门禁，但**尚未对真实 GitHub 验证过**：测试与演示走 `FORGE_REMOTE_PROVIDER=fake`，真实令牌与演示仓库仍缺（见 `docs/todo.md` 的 P1）。
- 幂等键覆盖 Agent 推进、推送和创建 PR；提交仍需审批与预览确认。
- 任务预算熔断已接入：最大轮次、总耗时、远程调用次数、连续失败阈值（`FORGE_TASK_MAX_*`），任一触顶即锁定并转人工复核。
- 已有本地 Agent 单步编排和后台循环；计划由规则/模板生成，模型通过 Anthropic 或 OpenAI 兼容接口起草。实际写入权限取决于上述模式。无沙箱容器，命令在主机上以受控白名单方式执行。
- 审批中心的拒绝、要求修改、过期列表与预算视图已接入。产物冒烟与组件渲染检查不能替代浏览器交互；设置等扩展入口仍未完成。
- 桌面壳（Electron）已接入并与浏览器路径并存：壳托管本地 API、状态落在 `userData`、原生目录选择器。窗口层在本受限工作区未真机验证（Windows ACL，见上面的前置条件）；没有打包分发配置，`apps/api` 仍以源码方式运行。
- 单一工作区根目录、单文件夹任务；Git 子目录保留所选范围并使用本地流程。多仓库、CI 交互、Review 评论、冲突处理与自动合并属于后续阶段。

各阶段勾选与验收口径以 `docs/phase-1-todolist.md`、`docs/phase-2-todolist.md` 为准；Phase 3（CI 交互、自动修复、冲突处理与治理）的范围见 `docs/phase-3-todolist.md`，其第 0 节列出了开工前必须先收口的 Phase 2 遗留项。
