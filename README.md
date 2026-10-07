# Coding Agent (Forge)

企业级 Coding / DevOps Agent 的 Monorepo：本地可运行、可审计、可恢复的研发闭环工作台。

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

> **Windows 上的一个已知前置条件**：Electron 启动时会检查 `node_modules/.../electron/dist` 是否允许 AppContainer 读取。若工作区处于受限账户 ACL 之下（本仓开发环境就是这样），会看到
> `FATAL:install_dir_access.cc ... none for ALL APPLICATION PACKAGES`，此时任何 Electron 应用（包括空应用）都起不来，与本项目代码无关。在**你自己的终端**里执行一次即可：
> `icacls "node_modules\.pnpm\electron@<版本>\node_modules\electron\dist" /grant *S-1-15-2-1:(OI)(CI)(RX)`
> 或者把仓库放在正常 ACL 路径下。当前分支的桌面壳就是在受限工作区里完成实现的，窗口层尚未真机验证。

## 演示流程

```bash
pnpm demo:init                        # 默认生成 .forge/demo/phase-1
python scripts/create_demo.py --name my-demo   # 需要其它名字时
```

该仓库自带一个真实缺陷：`clamp()` 未处理下边界，`test_calculator.py::test_clamp_below_minimum` 会失败。

在 Desktop 中按顺序执行：

1. 侧栏「添加 Git 仓库」→ 填入 `.forge/demo/phase-1` 的绝对路径。
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

## 验证

```bash
pnpm api:test     # API 单元 + 集成测试（会创建真实 Git 仓库夹具；远程段用 fake Provider，不出网）
pnpm api:init     # 创建/查看本地状态存储，并打印已保存的记录数
pnpm check        # Desktop 类型检查
pnpm build        # Desktop 生产构建
pnpm desktop:smoke # 校验构建产物完整性与关键样式/DOM 字符串（不是交互回放）
```

## 故障排查

- **`GIT_COMMAND_FAILED`**：确认 `git` 在 PATH 中，且 API 进程能进入目标仓库目录。
- **`GIT_UNAVAILABLE`（HTTP 503）**：API 进程连 `git` 子进程都拉不起来（Windows 上常见退出码 `3221225794` = `STATUS_DLL_INIT_FAILED`）。这属于该进程自身的启动上下文问题，从普通终端重启 API 即可；子进程环境块已由 `process_env.child_env()` 统一补全并清洗 `GIT_*`，因此不再依赖启动时的临时目录与 git 配置。
- **界面显示 0 仓库 0 任务但 API 有数据**：多为 CORS 或 API 地址不匹配，检查 `FORGE_CORS_ORIGINS` 与 `VITE_API_BASE_URL`。
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
- **`IDEMPOTENCY_IN_PROGRESS`（409）**：同键的另一个请求仍在执行中。
- **`PATCH_TOO_LARGE` / `PROTECTED_PATH` / `BINARY_FILE_NOT_ALLOWED`**：受 Phase 1 安全限制约束，缩小改动范围或排除受保护路径（`.env`、私钥、凭据文件、`.git`、依赖与构建目录）。

## 当前状态与已知限制

Phase 1（单仓库理解 → 计划 → 低风险修改 → 测试 → 审计 → 回滚）已完成并在浏览器与 API 两端验证。
Phase 2 的本地部分与远程代码路径均已完成：多轮编码、验证循环、审批门禁、任务分支、提交、推送、GitHub Provider 与 Draft PR、幂等键、持久化与恢复。

- 状态保存在本地 sqlite（默认 `.forge/state`，含检查点文件与提交/远程分支/PR 记录）；API 重启后未完成任务必须显式确认才继续写入。审计与测试输出仍无保留期限。
- 远程段已接通并带完整门禁，但**尚未对真实 GitHub 验证过**：测试与演示走 `FORGE_REMOTE_PROVIDER=fake`，真实令牌与演示仓库仍缺（见 `docs/todo.md` 的 P0）。
- 幂等键只覆盖有外部副作用的写端点（推送、创建 PR）；分支创建与提交由 `scope_hash` + 预览确认防重。
- 没有预算熔断：不限制最大轮次、总耗时与远程调用次数，连续失败不会自动转人工。
- 计划由规则/模板生成，尚未接入 LangGraph 等真实推理编排；无沙箱容器，命令在主机上以受控白名单方式执行。
- Desktop 的 `pnpm desktop:smoke` 只校验构建产物，交互流程仍靠浏览器人工走查；侧栏「Agent 技能 / 安全策略 / 设置」尚无行为；审批中心（拒绝/要求修改/过期列表）未实现。
- 桌面壳（Electron）已接入并与浏览器路径并存：壳托管本地 API、状态落在 `userData`、原生目录选择器。窗口层在本受限工作区未真机验证（Windows ACL，见上面的前置条件）；没有打包分发配置，`apps/api` 仍以源码方式运行。
- 单一工作区根目录、单仓库任务；多仓库、CI 交互、Review 评论、冲突处理与自动合并属于后续阶段。

各阶段勾选与验收口径以 `docs/phase-1-todolist.md`、`docs/phase-2-todolist.md` 为准；Phase 3（CI 交互、自动修复、冲突处理与治理）的范围见 `docs/phase-3-todolist.md`，其第 0 节列出了开工前必须先收口的 Phase 2 遗留项。
