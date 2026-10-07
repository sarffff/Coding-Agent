# Architecture

## Runtime boundaries

```text
apps/desktop (React + Vite + TypeScript)
  src/lib/api.ts ── VITE_API_BASE_URL / window.forge.status.apiBaseUrl ──► apps/api (FastAPI)
  src/lib/desktop.ts ── window.forge 桥（目录选择、工作区根目录、后端状态订阅）
  src/lib/preferences.tsx ── 语言 / 主题
  src/components/ ── RepositoryBrowser（浏览与编辑）、RunHistory（测试历史与回滚）
  src/App.tsx ── runs 视图内的「受控发布」面板：推送审批 → 推送 → Draft PR 预览/创建/对账
  electron/  ── 桌面壳：main(窗口/协议/IPC) + backend(uvicorn 生命周期) + config + preload
  packages/types ─► 共享领域类型   packages/ui ─► Dialog / AsyncMessage

apps/api/app/
  main.py                 路由、错误格式、trace id、跨服务编排与门禁
  services.py             用同一个状态存储装配全部服务（重启即重新构建）
  store.py                sqlite3 写穿存储与恢复序号
  config.py               FORGE_* 运行限制、工作区根目录与状态目录
  repository_service.py   仓库注册、路径归属校验、文件树、搜索、读文件、符号、仓库摘要
  file_policy.py          敏感与忽略路径的单一判定来源（搜索 / 补丁 / 提交共用）
  task_service.py         任务状态机、编码轮次、检查点记录、暂停/恢复/取消
  patch_service.py        diff 预览、乐观哈希校验、检查点快照、应用与回滚
  test_service.py         命令探测、白名单执行、超时与取消、输出截断、失败定位
  git_service.py          任务分支、提交预览（临时索引）、plumbing 提交与发布
  remote_provider.py      RemoteProvider 抽象 + GitHub REST + 内存 fake Provider
  remote_service.py       提交记录、任务分支推送、Draft PR 创建与远端状态对账
  idempotency.py          Idempotency-Key 回放与 TTL 清理（远程写接口）
  approval_service.py     审批请求、决策、过期与作用域失效
  audit_service.py        不可变审计事件（按 run 查询）
```

## Safety invariants

- 所有文件访问必须解析到 `FORGE_WORKSPACE_ROOT` 内的仓库目录，拒绝绝对路径、`..` 逃逸与符号链接逃逸。
- `file_policy.is_protected_path` 在搜索、补丁和提交三处一致拦截 `.env*`、私钥、凭据文件与依赖/构建目录。
- 写操作必须先预览：补丁要求显式 `confirm`，删除要求 `confirm_delete`，应用前创建文件快照检查点。
- 首次编码轮次需要 `plan` 审批；创建提交需要绑定同一 `scope_hash` 的 `write` 审批。
- 审批与提交绑定的是确切内容：`scope_hash` 覆盖 repository/branch/HEAD/tree/message，`validation_hash` 覆盖任务文件集合与 HEAD；文件或消息一变，旧审批与旧测试结果立即失效。
- 提交只包含任务文件：预览用临时索引计算 `write-tree`，发布用 `commit-tree` + `update-ref`，因此 hook 无法替换已审阅的树，用户的其它暂存内容也不被覆盖。
- 只允许在任务分支上提交与推送，`main`/`master`/`develop`/`production`/`release` 被拒绝（`git_service.PROTECTED_BRANCHES` 是唯一来源）。
- 推送只发布当前任务分支，命令为 `git push <remote> refs/heads/<b>:refs/heads/<b>`：无 `--force`、无 `--mirror`、无删除分支；`GIT_TERMINAL_PROMPT=0` 保证凭据缺失时立即失败而不是挂起。
- 推送前必须同时满足：`push` 审批、存在已审批的本地提交、工作区当前就在任务分支、`HEAD` 仍等于该提交（否则 `REMOTE_HEAD_MOVED`）；远程 URL 必须命中 `FORGE_ALLOWED_REMOTE_HOSTS` 与可选的 `FORGE_ALLOWED_REPOSITORIES`，未配置远程时报 `REMOTE_NOT_CONFIGURED`，绝不猜测地址。
- 创建 PR 前必须满足：`pr` 审批、该任务分支已推送、远程仓库与源/目标分支仍存在；重复创建由 `Idempotency-Key` 与 GitHub 的 422「已存在 PR」回查共同收敛。
- 凭据边界：`FORGE_GITHUB_TOKEN` 只用于 REST 请求头，`git push` 使用操作者自己的凭据助手；令牌不写库、不入审计，推送 stderr 中的令牌被替换为 `***`。
- 命令执行使用参数数组（`shell=False`）、可执行文件白名单、超时、输出上限与取消；`GIT_*` 环境变量在调用前被清洗。
- 错误响应统一为 `code` / `message` / `details` / `trace_id`；不记录文件内容与凭据。
- 桌面壳只把 API 绑在 `127.0.0.1` 的临时端口上，并在认领端口前确认它空闲且无人应答 `/health`——不会把别人的服务当成自己的。
- 渲染进程固定 `contextIsolation + sandbox + 无 nodeIntegration`，暴露面仅 `window.forge`；外链走系统浏览器，`<webview>` 被阻止，导航锁在 `forge-app://desktop` 内，协议处理器对路径做越界检查。

## Desktop shell

`apps/desktop/electron/` 是可选项：`pnpm start` 的浏览器路径完全不依赖它。壳启动顺序固定为「解析解释器 → 选端口 → 起 uvicorn → 轮询 `/health` → 建窗口」，因此渲染进程可以在模块顶层同步读到 `window.forge.status.apiBaseUrl`；后端起不来时窗口显示一份自解释的错误页（含日志路径），而不是一个空白工作台。配置（工作区根目录、记住的端口）写在 `userData/forge-desktop.json`，状态与日志在 `userData/state`、`userData/logs`。窗口层在本仓库的受限工作区里无法真机验证（见 README 的 Windows ACL 前置条件），但 `backend.cjs` 的解释器解析、端口避让、健康等待、`forge-app://desktop` 的 CORS 预检与终止清理已用真实 uvicorn 进程验证过。

## Persistence

`app/services.py:build_services()` 用同一个 `StateStore`（`app/store.py`，标准库 sqlite3，无额外依赖）装配全部服务：仓库、任务（含轮次与任务检查点）、审批、审计事件、文件检查点元数据，以及提交、远程分支与 PR 记录写入 `FORGE_STATE_DIR/state.db`（默认 `.forge/state`），检查点的文件内容写在 `state.db` 同级的 `checkpoints/<id>/` 下。schema 版本记录在 `meta` 表，旧库通过 `_migrate()` 增量升级（当前 1→2 增加 `idempotency` 表），比当前版本更新的库直接拒绝打开。

重启等于再调用一次 `build_services()`：记录按插入顺序回灌，处于执行中状态的任务被标记 `requires_recovery`，此后除回滚、取消、查询与 `POST /api/v1/tasks/{id}/recover` 之外的写端点一律返回 `RECOVERY_CONFIRMATION_REQUIRED`，不会静默续跑。补丁预览（`patch_id`）是进程内对象，不持久化：重启后需要重新预览，这是有意为之——未经审阅的 diff 不应该跨进程存活。

`Idempotency-Key` 只用于有外部副作用的写端点（`POST /tasks/{id}/push`、`POST /tasks/{id}/pull-request`）：首次响应体连同状态码入库，同键同请求体直接回放首次结果，同键不同请求体或同键并发在途分别返回 `IDEMPOTENCY_KEY_REUSED` 与 `IDEMPOTENCY_IN_PROGRESS`；记录按 `FORGE_IDEMPOTENCY_TTL_HOURS` 滚动清理。

## Phase status

- Phase 1：单仓库理解 → 计划 → 低风险修改 → 测试 → 审计 → 回滚，已完成。
- Phase 2：多轮编码、验证循环、审批门禁、任务分支、提交、推送、GitHub Provider 与 Draft PR、幂等键与持久化已完成；审批中心的拒绝/要求修改流程、预算熔断与记录保留期限未完成。
- Phase 3（CI 交互、自动修复、冲突处理、治理）：范围见 `docs/phase-3-todolist.md`。

验收口径以 `docs/phase-1-todolist.md` 与 `docs/phase-2-todolist.md` 的勾选为准。
