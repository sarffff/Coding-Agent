# Coding Agent

企业级 Coding / DevOps Agent 的 Monorepo 基础架构。

## 目录

```text
apps/
  desktop/   React + Vite + TypeScript 桌面工作台
  api/       FastAPI 服务骨架
packages/
  (预留共享 UI、类型与配置包)
docs/
  architecture.md
```

## 本地运行

```bash
pnpm install
pnpm dev
```

API 服务：

```bash
pnpm api:dev
```

前后端一起启动：

```bash
pnpm start
```

默认地址：Desktop `http://127.0.0.1:5173`，API `http://127.0.0.1:8000`。

API 默认只允许访问启动目录下的仓库。复制 `apps/api/.env.example` 为 `.env`，通过 `FORGE_WORKSPACE_ROOT` 指定本地工作区根目录；Desktop 可通过 `apps/desktop/.env.example` 配置 API 地址。

当前 desktop 端使用 mock 数据展示 Phase 1 的工作台骨架，已预留 `VITE_API_BASE_URL` 与 API client；后续将把任务流、执行轨迹、审批节点和仓库操作接入 API。

Phase 1 的执行清单见 [docs/phase-1-todolist.md](docs/phase-1-todolist.md)。
