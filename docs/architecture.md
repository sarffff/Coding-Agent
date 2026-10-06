# Architecture

## Runtime boundaries

```text
apps/desktop (React + Vite + TypeScript)
  src/lib/api.ts ── VITE_API_BASE_URL ──► apps/api (FastAPI)
  src/lib/preferences.tsx ── 语言 / 主题
  src/components/ ── RepositoryBrowser（浏览与编辑）、RunHistory（测试历史与回滚）
  packages/types ─► 共享领域类型   packages/ui ─► Dialog / AsyncMessage

apps/api/app/
  main.py                 路由、错误格式、trace id、跨服务编排与门禁
  config.py               FORGE_* 运行限制与工作区根目录
  repository_service.py   仓库注册、路径归属校验、文件树、搜索、读文件、符号、仓库摘要
  file_policy.py          敏感与忽略路径的单一判定来源（搜索 / 补丁 / 提交共用）
  task_service.py         任务状态机、编码轮次、检查点记录、暂停/恢复/取消
  patch_service.py        diff 预览、乐观哈希校验、检查点快照、应用与回滚
  test_service.py         命令探测、白名单执行、超时与取消、输出截断、失败定位
  git_service.py          任务分支、提交预览（临时索引）、plumbing 提交与发布
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
- 只允许在任务分支上提交，`main`/`master`/`develop`/`production` 被拒绝；推送、强制推送与远程分支删除尚无端点。
- 命令执行使用参数数组（`shell=False`）、可执行文件白名单、超时、输出上限与取消；`GIT_*` 环境变量在调用前被清洗。
- 错误响应统一为 `code` / `message` / `details` / `trace_id`；不记录文件内容与凭据。

## Persistence

当前全部状态保存在进程内存（`repository_service`、`task_service`、`patch_service`、`approval_service`、`audit_service` 各自的 dict/list），API 重启即丢失，也不存在“重启后需人工确认”的恢复语义。持久化与恢复是 Phase 2 §6 的未完成项。

## Phase status

- Phase 1：单仓库理解 → 计划 → 低风险修改 → 测试 → 审计 → 回滚，已完成。
- Phase 2：多轮编码、验证循环、审批门禁、任务分支与提交已完成；推送、Remote Provider、PR/MR、幂等键与持久化未开始。
- Phase 3（CI 交互、自动修复、冲突处理、治理）：范围见 `docs/phase-3-todolist.md`。

验收口径以 `docs/phase-1-todolist.md` 与 `docs/phase-2-todolist.md` 的勾选为准。
