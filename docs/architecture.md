# Foundation architecture

## Runtime boundaries

```text
desktop (React + Vite + TypeScript)
  └─ src/lib/api.ts → API_BASE_URL
                         │
                         ▼
api (FastAPI / Python)
  ├─ /health
  └─ /api/v1/workspace/summary
```

## Monorepo boundaries

- `apps/desktop`: 工作台 UI、任务输入、任务队列、执行轨迹、审批入口。
- `apps/api`: Python API 边界，后续接入 LangGraph、MCP、仓库索引与沙箱执行。
- `packages/types`: 前后端共享领域类型。
- `packages/ui`: 共享组件包预留位置。

Desktop 端当前用 mock 任务数据保证界面可独立开发，API client 已预留真实服务接入点。
