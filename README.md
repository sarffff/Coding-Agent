# Coding Agent (Forge)

企业级 Coding / DevOps Agent 的 Monorepo：本地可运行、可审计、可恢复的研发闭环工作台。

## 目录

```text
apps/
  desktop/   React + Vite + TypeScript 工作台
  api/       FastAPI 服务（仓库接入、编码循环、审批、Git、审计）
packages/
  types/     前后端共享领域类型
  ui/        共享组件（Dialog、AsyncMessage）
docs/
  architecture.md
  phase-1-todolist.md
  phase-2-todolist.md
examples/
  phase-1-demo/   演示仓库模板源文件
scripts/
  create_demo.py  生成隔离的本地演示 Git 仓库
```

## 环境要求

- Node.js 20+，pnpm 12
- Python 3.11+（开发与验证使用 3.14）
- 可用的 `git` 命令行

## 安装与启动

```bash
pnpm install
pip install -r apps/api/requirements-dev.txt

pnpm start          # 同时启动 API 与 Desktop（run.js）
pnpm api:dev        # 只启动 API：http://127.0.0.1:8000
pnpm dev            # 只启动 Desktop：http://127.0.0.1:5173
```

## 配置

| 变量 | 位置 | 作用 | 默认值 |
|---|---|---|---|
| `FORGE_WORKSPACE_ROOT` | `apps/api/.env` | API 允许读写的仓库根目录，所有路径都必须落在其内 | 进程启动目录 |
| `FORGE_MAX_FILE_SIZE_BYTES` | 同上 | 单文件读写上限 | 1000000 |
| `FORGE_MAX_TREE_ENTRIES` / `FORGE_MAX_SEARCH_RESULTS` | 同上 | 树与搜索的返回上限 | 5000 / 200 |
| `FORGE_GIT_TIMEOUT_SECONDS` / `FORGE_COMMAND_TIMEOUT_SECONDS` | 同上 | Git 与测试命令超时 | 10 / 30 |
| `FORGE_CORS_ORIGINS` | 同上 | 允许访问 API 的源 | `http://localhost:5173`、`http://127.0.0.1:5173` |
| `VITE_API_BASE_URL` | `apps/desktop/.env` | Desktop 连接的 API 地址 | `http://127.0.0.1:8000` |

复制 `apps/api/.env.example` 与 `apps/desktop/.env.example` 为 `.env` 后再修改。

> CORS 默认只放行 5173。Desktop 运行在其他端口时必须把该端口加入 `FORGE_CORS_ORIGINS`，否则请求会被浏览器拦截，界面表现为“没有仓库、没有任务”。

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

命令行等价流程见 `docs/phase-1-todolist.md` 的「推荐接口顺序」。

## 验证

```bash
pnpm api:test     # API 单元 + 集成测试（会创建真实 Git 仓库夹具）
pnpm check        # Desktop 类型检查
pnpm build        # Desktop 生产构建
```

## 故障排查

- **`GIT_COMMAND_FAILED`**：确认 `git` 在 PATH 中，且 API 进程能进入目标仓库目录。Windows 上若长时间运行的重载进程报 `exit_code 3221225794`（0xC0000142），重启 API 进程即可，属于进程环境问题而非代码问题。
- **界面显示 0 仓库 0 任务但 API 有数据**：多为 CORS 或 API 地址不匹配，检查 `FORGE_CORS_ORIGINS` 与 `VITE_API_BASE_URL`。
- **`WORKTREE_NOT_CLEAN`**：任务分支必须在干净工作区上创建，先提交或还原本地改动。
- **`COMMIT_PREVIEW_STALE` / `TEST_RESULTS_STALE`**：审批绑定的是具体的 diff 与验证结果；改动文件或消息后需重新预览、重新审批。
- **`APPROVAL_REQUIRED`**：首次编码轮次需要方案审批，创建提交需要写入审批。
- **`PATCH_TOO_LARGE` / `PROTECTED_PATH` / `BINARY_FILE_NOT_ALLOWED`**：受 Phase 1 安全限制约束，缩小改动范围或排除受保护路径（`.env`、私钥、凭据文件、`.git`、依赖与构建目录）。

## 当前状态与已知限制

Phase 1（单仓库理解 → 计划 → 低风险修改 → 测试 → 审计 → 回滚）已完成并在浏览器与 API 两端验证。
Phase 2 的本地部分（多轮编码、验证循环、审批门禁、任务分支与提交）已完成；**远程部分尚未实现**。

- 任务、仓库、审批、检查点与审计事件全部保存在**进程内存**中，重启 API 即丢失，也没有“重启后需人工确认”的恢复语义。
- 没有推送、远程 Provider（GitHub/GitLab）、PR/MR 创建与查询；`push`、`pr` 审批类型只是占位。
- 没有幂等键：重复请求可能重复创建分支或提交。
- 没有预算熔断：不限制最大轮次、总耗时与远程调用次数，连续失败不会自动转人工。
- 计划由规则/模板生成，尚未接入 LangGraph 等真实推理编排；无沙箱容器，命令在主机上以受控白名单方式执行。
- Desktop 无自动化测试；侧栏「Agent 技能 / 安全策略 / 设置」尚无行为；无键盘快捷键。
- 单一工作区根目录、单仓库任务；多仓库、CI 交互、冲突处理与自动合并属于后续阶段。

各阶段勾选与验收口径以 `docs/phase-1-todolist.md`、`docs/phase-2-todolist.md` 为准。
