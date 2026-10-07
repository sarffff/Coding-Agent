# Coding-Agent 当前进度与后续 TODO

## 当前判断（2026-10-07）

- 分支 `feat-1006-finishPhase3`，领先 `main` 11 个提交；本轮改动尚未提交。
- Phase 1：69/69，首页加载/错误/超时/重试、快捷键、浅色主题 AA 覆盖与产物冒烟检查已补齐。
- Phase 2：71/117。远程段已从死代码接成可用闭环——`RemoteProvider` 抽象 + GitHub REST + 内存 `fake` Provider、任务分支推送、Draft PR 预览/创建/对账、`Idempotency-Key` 回放、提交/远程分支/PR 记录写穿 sqlite（schema 1→2 迁移），Desktop 新增「受控发布」面板。
- Phase 3：4/85，尚未满足进入条件（缺真实演示仓库与令牌）。
- 验证基线：`pnpm api:test` 43 passed / 1 skipped（含 `test_remote_workflow.py` 10 项）、`pnpm check` 通过、`pnpm build` 通过、`pnpm desktop:smoke` 通过；推送→审批→PR 全流程已在真实浏览器走通（`fake` Provider，未产生外部副作用）。
- 桌面壳（Electron）已接入：`pnpm desktop:electron` / `:dev` / `:smoke`，壳自己拉起并托管 uvicorn（`127.0.0.1` 临时端口 + 端口记忆 + 不认领他人服务）、`forge-app://desktop` 特权协议解决 CORS、状态与日志落到 `userData`、原生目录选择器维护工作区根目录与仓库路径。**窗口层在本受限工作区无法真机验证**：连 20 行的空 Electron 应用都会因 `electron/dist` 缺少 ALL APPLICATION PACKAGES 只读 ACE 而 FATAL（`--no-sandbox` 无效）；`backend.cjs` 全链路已用真实 uvicorn 进程验证。
- 已知偏差：`pnpm desktop:smoke` 校验的是构建产物完整性与关键样式/DOM 字符串，不是交互回放；真实交互回放目前靠浏览器手工走查。

## 后续实施顺序

### P0：让桌面壳真正跑起来 + 把真实远程链路打通

- 在正常 ACL 的环境里启动 Electron（本仓 README 给了 `icacls` 一行修法，或把仓库放在普通目录），跑 `pnpm desktop:electron:smoke` 确认窗口层：协议加载、`window.forge` 桥、工作区根目录更换后的后端重启与端口保持。
- 准备仅用于开发和测试的 GitHub 仓库与单仓库 fine-grained PAT，通过 `FORGE_GITHUB_TOKEN` 注入，`FORGE_ALLOWED_REMOTE_HOSTS`/`FORGE_ALLOWED_REPOSITORIES` 收紧范围。
- 对真实 GitHub 跑一次推送 + Draft PR + `pull-request/refresh` 对账，确认 401/403/422/429/504 的实际语义与状态码映射；测试仍需 fake Provider 保证可重复。
- `git push` 使用操作者自己的凭据助手，`FORGE_GITHUB_TOKEN` 只用于 REST：这条边界要在 README 与演示脚本里写清，并验证凭据缺失时 `GIT_TERMINAL_PROMPT=0` 会立即失败而不是挂起。

### P1：补齐远程段的观测与治理

- 远程调用记录耗时、HTTP 状态码与重试次数（不含令牌与完整响应体）。
- 审批中心：待审批/已批准/已拒绝/需修改/已过期列表，拒绝时强制填写原因，`request_changes` 后能直接开下一轮。
- 审计与测试输出的保留期限与上限（幂等记录已按 `FORGE_IDEMPOTENCY_TTL_HOURS` 清理，其余仍会无限增长）。
- 预算熔断：最大轮次、总耗时、远程调用次数；连续失败达阈值转人工。

### P2：进入 Phase 3

- 以真实 PR 为入口接入 CI 状态、Review 评论与冲突处理；范围与进入条件见 `docs/phase-3-todolist.md`。
- 先决条件：P0 已验证 + P1 的审批中心与预算熔断落地。
