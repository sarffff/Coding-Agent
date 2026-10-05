import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Bot,
  Box,
  Check,
  ChevronDown,
  CircleDot,
  Clock3,
  Code2,
  Command,
  FileCode2,
  GitBranch,
  GitPullRequest,
  LayoutDashboard,
  LockKeyhole,
  Menu,
  MoreHorizontal,
  PackageCheck,
  Plus,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  X,
  Zap,
} from "lucide-react";
import {
  applyPatch,
  createTask,
  getApiHealth,
  getTask,
  getTaskGitSnapshot,
  getWorkspaceSummary,
  listIterations,
  listApprovals,
  listRepositories,
  listRunEvents,
  listTasks,
  previewPatch,
  previewCommit,
  registerRepository,
  resumeTask,
  runTests,
  requestApproval,
  createCommit,
  createTaskBranch,
  decideApproval,
  startIteration,
  startRepair,
  pauseTask,
  cancelTask,
  type AgentTask,
  type AuditEvent,
  type CommitPreview,
  type GitSnapshot,
  type Approval,
  type PatchPreview,
  type Repository,
  type TestRunResult,
  type TaskIteration,
  type WorkspaceSummary,
} from "./lib/api";

type NavKey = "overview" | "tasks" | "repositories" | "runs" | "approvals";

type Task = {
  id: string;
  runId?: string;
  repositoryId?: string | null;
  currentIteration: number;
  retryCount: number;
  nextAction: string | null;
  title: string;
  repo: string;
  branch: string;
  status: "running" | "queued" | "review" | "done" | "failed" | "paused" | "cancelled";
  time: string;
  accent: string;
  plan?: AgentTask["plan"];
};

type StreamEvent = {
  time: string;
  title: string;
  detail: string;
  icon: "search" | "code" | "test" | "approve";
  status: "done" | "active" | "waiting";
};

const initialTasks: Task[] = [];

function taskFromApi(task: AgentTask): Task {
  return {
    id: task.id,
    runId: task.run_id,
    repositoryId: task.repository_id,
    currentIteration: task.current_iteration,
    retryCount: task.retry_count,
    nextAction: task.next_action,
    title: task.goal,
    repo: task.plan?.repository_summary ?? "workspace",
    branch: task.run_id,
    status: task.status === "planning" || task.status === "queued" ? "queued" : task.status === "failed" ? "failed" : task.status === "cancelled" ? "cancelled" : task.status === "paused" ? "paused" : task.status === "done" || task.status === "completed" || task.status === "pr_created" ? "done" : task.status === "review" || task.status === "awaiting_approval" || task.status === "ready_for_pr" ? "review" : "running",
    time: "now",
    accent: task.status === "failed" ? "rose" : "amber",
    plan: task.plan,
  };
}

const navItems: Array<{ key: NavKey; label: string; icon: typeof LayoutDashboard; count?: number }> = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "tasks", label: "Task queue", icon: Zap, count: 4 },
  { key: "repositories", label: "Repositories", icon: GitBranch },
  { key: "runs", label: "Execution runs", icon: TerminalSquare },
  { key: "approvals", label: "Approvals", icon: ShieldCheck, count: 2 },
];

function eventToStreamEvent(event: AuditEvent): StreamEvent {
  const action = event.action.toLowerCase();
  const icon: StreamEvent["icon"] = action.includes("test") ? "test" : action.includes("patch") ? "code" : action.includes("repository") || action.includes("search") ? "search" : "approve";
  const title = action === "task.create" ? "Task created" : action === "patch.preview" ? "Patch preview ready" : action === "patch.apply" ? "Patch applied" : action === "tests.run" ? "Tests completed" : action === "checkpoint.rollback" ? "Checkpoint restored" : event.action;
  const details = event.details;
  const command = Array.isArray(details.command) ? details.command.join(" ") : undefined;
  const detail = command ? `${event.summary} · ${command}` : event.summary;
  return {
    time: new Date(event.created_at).toLocaleTimeString([], { hour12: false }),
    title,
    detail,
    icon,
    status: event.status === "succeeded" ? "done" : event.status === "failed" ? "waiting" : "active",
  };
}

const iconForEvent = (icon: StreamEvent["icon"]) => {
  if (icon === "search") return Search;
  if (icon === "code") return FileCode2;
  if (icon === "test") return PackageCheck;
  return ShieldCheck;
};

function App() {
  const [activeNav, setActiveNav] = useState<NavKey>("overview");
  const [tasks, setTasks] = useState(initialTasks);
  const [activeTaskId, setActiveTaskId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [apiStatus, setApiStatus] = useState<"checking" | "connected" | "offline">("checking");
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [repositoryError, setRepositoryError] = useState<string | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [workspaceSummary, setWorkspaceSummary] = useState<WorkspaceSummary | null>(null);
  const [runEvents, setRunEvents] = useState<AuditEvent[]>([]);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestRunResult | null>(null);
  const [testRunning, setTestRunning] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [patchPath, setPatchPath] = useState("");
  const [patchContent, setPatchContent] = useState("");
  const [patchPreviewResult, setPatchPreviewResult] = useState<PatchPreview | null>(null);
  const [patchRunning, setPatchRunning] = useState(false);
  const [patchApplied, setPatchApplied] = useState<string | null>(null);
  const [patchError, setPatchError] = useState<string | null>(null);
  const [iterations, setIterations] = useState<TaskIteration[]>([]);
  const [iterationRunning, setIterationRunning] = useState(false);
  const [iterationError, setIterationError] = useState<string | null>(null);
  const [gitSnapshot, setGitSnapshot] = useState<GitSnapshot | null>(null);
  const [branchName, setBranchName] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const [commitPreviewResult, setCommitPreviewResult] = useState<CommitPreview | null>(null);
  const [gitRunning, setGitRunning] = useState(false);
  const [gitError, setGitError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);

  const activeTask = useMemo(
    () => tasks.find((task) => task.id === activeTaskId) ?? tasks[0],
    [activeTaskId, tasks],
  );

  useEffect(() => {
    const controller = new AbortController();
    getApiHealth(controller.signal)
      .then(() => setApiStatus("connected"))
      .catch(() => setApiStatus("offline"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const runId = activeTask?.runId;
    if (!runId) {
      setRunEvents([]);
      setEventsError(null);
      return;
    }
    let disposed = false;
    const loadEvents = async () => {
      try {
        const payload = await listRunEvents(runId);
        if (!disposed) {
          setRunEvents(payload.items);
          setEventsError(null);
        }
      } catch (error) {
        if (!disposed) setEventsError(error instanceof Error ? error.message : "执行事件加载失败");
      }
    };
    void loadEvents();
    const timer = window.setInterval(() => void loadEvents(), 4000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [activeTask?.runId]);

  useEffect(() => {
    setTestResult(null);
    setTestError(null);
    setPatchPreviewResult(null);
    setPatchApplied(null);
    setPatchError(null);
    setPatchPath("");
    setPatchContent("");
  }, [activeTaskId]);

  useEffect(() => {
    if (!activeTaskId || !activeTask?.repositoryId) {
      setGitSnapshot(null);
      return;
    }
    const controller = new AbortController();
    getTaskGitSnapshot(activeTaskId, controller.signal).then(setGitSnapshot).catch(() => setGitSnapshot(null));
    return () => controller.abort();
  }, [activeTaskId, activeTask?.repositoryId]);

  useEffect(() => {
    if (!activeTaskId) {
      setApprovals([]);
      return;
    }
    const controller = new AbortController();
    listApprovals(activeTaskId, controller.signal).then((payload) => setApprovals(payload.items)).catch(() => setApprovals([]));
    return () => controller.abort();
  }, [activeTaskId]);

  useEffect(() => {
    if (!activeTaskId) {
      setIterations([]);
      return;
    }
    const controller = new AbortController();
    listIterations(activeTaskId, controller.signal).then((payload) => setIterations(payload.items)).catch(() => setIterations([]));
    return () => controller.abort();
  }, [activeTaskId]);

  useEffect(() => {
    const controller = new AbortController();
    getWorkspaceSummary(controller.signal).then(setWorkspaceSummary).catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listTasks(controller.signal)
      .then((payload) => {
        if (payload.items.length) {
          const nextTasks = payload.items.map(taskFromApi);
          setTasks((current) => [...nextTasks, ...current.filter((task) => !nextTasks.some((item) => item.id === task.id))]);
          setActiveTaskId(nextTasks[0].id);
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listRepositories(controller.signal)
      .then((payload) => setRepositories(payload.items))
      .catch(() => setRepositories([]));
    return () => controller.abort();
  }, []);

  const pageTitle = navItems.find((item) => item.key === activeNav)?.label ?? "Overview";
  const displayedStreamEvents = runEvents.map(eventToStreamEvent);
  const reviewTasks = tasks.filter((task) => task.status === "review");
  const planApproval = approvals.find((item) => item.type === "plan");
  const writeApproval = approvals.find((item) => item.type === "write");

  const runTask = async () => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt || isRunning) return;
    setTaskError(null);
    setIsRunning(true);
    setPrompt("");
    try {
      const task = await createTask(trimmedPrompt, repositories[0]?.id);
      const newTask = taskFromApi(task);
      setTasks((current) => [newTask, ...current.filter((item) => item.id !== newTask.id)]);
      setActiveTaskId(newTask.id);
    } catch (error) {
      setTaskError(error instanceof Error ? error.message : "任务创建失败");
      setPrompt(trimmedPrompt);
    } finally {
      setIsRunning(false);
    }
  };

  const addRepository = async () => {
    const path = window.prompt("输入本地 Git 仓库路径");
    if (!path?.trim()) return;
    setRepositoryError(null);
    try {
      const repository = await registerRepository(path.trim());
      setRepositories((current) => [repository, ...current.filter((item) => item.id !== repository.id)]);
    } catch (error) {
      setRepositoryError(error instanceof Error ? error.message : "仓库添加失败");
    }
  };

  const executeTests = async () => {
    if (!activeTask?.runId || testRunning) return;
    setTestRunning(true);
    setTestError(null);
    try {
      const result = await runTests(activeTask.runId);
      setTestResult(result);
      const [events, nextIterations, task] = await Promise.all([
        listRunEvents(activeTask.runId),
        listIterations(activeTask.id),
        getTask(activeTask.id),
      ]);
      setRunEvents(events.items);
      setIterations(nextIterations.items);
      updateTaskFromApi(task);
    } catch (error) {
      setTestError(error instanceof Error ? error.message : "测试执行失败");
    } finally {
      setTestRunning(false);
    }
  };

  const previewActivePatch = async () => {
    if (!activeTask?.runId || !patchPath.trim() || patchRunning) return;
    setPatchRunning(true);
    setPatchError(null);
    setPatchApplied(null);
    try {
      const preview = await previewPatch(activeTask.runId, [{ path: patchPath.trim(), content: patchContent, operation: "update" }]);
      setPatchPreviewResult(preview);
      const events = await listRunEvents(activeTask.runId);
      setRunEvents(events.items);
    } catch (error) {
      setPatchError(error instanceof Error ? error.message : "Patch 预览失败");
    } finally {
      setPatchRunning(false);
    }
  };

  const applyActivePatch = async () => {
    if (!activeTask?.runId || !patchPreviewResult || patchRunning) return;
    setPatchRunning(true);
    setPatchError(null);
    try {
      const result = await applyPatch(activeTask.runId, patchPreviewResult.patch_id, true);
      setPatchApplied(`已创建检查点 ${result.checkpoint.id}，应用 ${result.applied_files.length} 个文件`);
      const [events, repositoriesPayload] = await Promise.all([
        listRunEvents(activeTask.runId),
        listRepositories(),
      ]);
      setRunEvents(events.items);
      setRepositories(repositoriesPayload.items);
      const [nextIterations, task] = await Promise.all([listIterations(activeTask.id), getTask(activeTask.id)]);
      setIterations(nextIterations.items);
      updateTaskFromApi(task);
    } catch (error) {
      setPatchError(error instanceof Error ? error.message : "Patch 应用失败");
    } finally {
      setPatchRunning(false);
    }
  };

  const cancelPatchPreview = () => {
    setPatchPreviewResult(null);
    setPatchApplied(null);
    setPatchError(null);
  };

  const createBranch = async () => {
    if (!activeTask || !branchName.trim() || gitRunning) return;
    setGitRunning(true);
    setGitError(null);
    try {
      const snapshot = await createTaskBranch(activeTask.id, branchName.trim());
      setGitSnapshot(snapshot);
      setBranchName("");
      const task = await getTask(activeTask.id);
      updateTaskFromApi(task);
    } catch (error) {
      setGitError(error instanceof Error ? error.message : "任务分支创建失败");
    } finally {
      setGitRunning(false);
    }
  };

  const previewTaskCommit = async () => {
    if (!activeTask || !commitMessage.trim() || gitRunning) return;
    setGitRunning(true);
    setGitError(null);
    try {
      setCommitPreviewResult(await previewCommit(activeTask.id, commitMessage.trim()));
    } catch (error) {
      setGitError(error instanceof Error ? error.message : "提交预览失败");
    } finally {
      setGitRunning(false);
    }
  };

  const commitTask = async () => {
    if (!activeTask || !commitPreviewResult?.ready || gitRunning) return;
    setGitRunning(true);
    setGitError(null);
    try {
      const snapshot = await createCommit(activeTask.id, commitMessage.trim());
      setGitSnapshot(snapshot);
      setCommitPreviewResult(null);
      const task = await getTask(activeTask.id);
      updateTaskFromApi(task);
    } catch (error) {
      setGitError(error instanceof Error ? error.message : "提交创建失败");
    } finally {
      setGitRunning(false);
    }
  };

  const requestWriteApproval = async () => {
    if (!activeTask || !commitPreviewResult || gitRunning) return;
    setGitRunning(true);
    setGitError(null);
    try {
      const approval = await requestApproval(activeTask.id, "write", `Approve commit: ${commitPreviewResult.commit_message}`, commitPreviewResult.scope_hash);
      setApprovals((current) => [approval, ...current.filter((item) => item.id !== approval.id)]);
    } catch (error) {
      setGitError(error instanceof Error ? error.message : "写入审批请求失败");
    } finally {
      setGitRunning(false);
    }
  };

  const approveWrite = async () => {
    const approval = approvals.find((item) => item.type === "write" && item.status === "pending");
    if (!approval || gitRunning) return;
    setGitRunning(true);
    setGitError(null);
    try {
      const decided = await decideApproval(approval.id, "approve");
      setApprovals((current) => current.map((item) => item.id === decided.id ? decided : item));
    } catch (error) {
      setGitError(error instanceof Error ? error.message : "写入审批决策失败");
    } finally {
      setGitRunning(false);
    }
  };

  const updateTaskFromApi = (task: AgentTask) => {
    const nextTask = taskFromApi(task);
    setTasks((current) => [nextTask, ...current.filter((item) => item.id !== nextTask.id)]);
    setActiveTaskId(nextTask.id);
  };

  const beginIteration = async () => {
    if (!activeTask || iterationRunning) return;
    setIterationRunning(true);
    setIterationError(null);
    try {
      const iteration = await startIteration(activeTask.id);
      setIterations((current) => [...current, iteration]);
      const task = await getTask(activeTask.id);
      updateTaskFromApi(task);
    } catch (error) {
      setIterationError(error instanceof Error ? error.message : "编码轮次启动失败");
    } finally {
      setIterationRunning(false);
    }
  };

  const requestPlanApproval = async () => {
    if (!activeTask || iterationRunning) return;
    setIterationRunning(true);
    setIterationError(null);
    try {
      const approval = await requestApproval(activeTask.id, "plan", `Approve the plan for: ${activeTask.title}`);
      setApprovals((current) => [approval, ...current.filter((item) => item.id !== approval.id)]);
    } catch (error) {
      setIterationError(error instanceof Error ? error.message : "审批请求创建失败");
    } finally {
      setIterationRunning(false);
    }
  };

  const approvePlan = async () => {
    const approval = approvals.find((item) => item.type === "plan" && item.status === "pending");
    if (!approval || iterationRunning) return;
    setIterationRunning(true);
    setIterationError(null);
    try {
      const decided = await decideApproval(approval.id, "approve");
      setApprovals((current) => current.map((item) => item.id === decided.id ? decided : item));
    } catch (error) {
      setIterationError(error instanceof Error ? error.message : "审批决策失败");
    } finally {
      setIterationRunning(false);
    }
  };

  const beginRepair = async () => {
    if (!activeTask || iterationRunning) return;
    setIterationRunning(true);
    setIterationError(null);
    try {
      const iteration = await startRepair(activeTask.id);
      setIterations((current) => [...current, iteration]);
      const task = await getTask(activeTask.id);
      updateTaskFromApi(task);
    } catch (error) {
      setIterationError(error instanceof Error ? error.message : "修复轮次启动失败");
    } finally {
      setIterationRunning(false);
    }
  };

  const changeTaskState = async (action: "pause" | "resume" | "cancel") => {
    if (!activeTask || iterationRunning) return;
    if (action === "cancel" && !window.confirm("Cancel this task? The current checkpoint will remain available.")) return;
    setIterationRunning(true);
    setIterationError(null);
    try {
      const result = action === "pause" ? await pauseTask(activeTask.id) : action === "resume" ? await resumeTask(activeTask.id) : await cancelTask(activeTask.id);
      updateTaskFromApi(result.task);
    } catch (error) {
      setIterationError(error instanceof Error ? error.message : "任务状态更新失败");
    } finally {
      setIterationRunning(false);
    }
  };

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark">F</div>
          <div>
            <div className="brand-name">FORGE</div>
            <div className="brand-subtitle">coding agent / 0.1</div>
          </div>
          <button className="icon-button mobile-close" aria-label="Close navigation" onClick={() => setSidebarOpen(false)}>
            <X size={17} />
          </button>
        </div>

        <button className="workspace-switcher" onClick={() => void addRepository()} title="添加本地 Git 仓库">
          <span className="workspace-icon"><Code2 size={15} /></span>
          <span className="workspace-copy">
            <strong>{repositories[0]?.name ?? "No repository"}</strong>
            <small>{repositories[0] ? "local workspace" : "add a Git repository"}</small>
          </span>
          <ChevronDown size={15} />
        </button>

        <div className="nav-label">Workspace</div>
        <nav className="primary-nav" aria-label="Primary navigation">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                className={`nav-item ${activeNav === item.key ? "nav-item--active" : ""}`}
                onClick={() => {
                  setActiveNav(item.key);
                  setSidebarOpen(false);
                }}
              >
                <Icon size={17} strokeWidth={1.8} />
                <span>{item.label}</span>
                {(item.key === "tasks" ? tasks.length : item.key === "approvals" ? reviewTasks.length : item.count) ? <span className="nav-count">{item.key === "tasks" ? tasks.length : item.key === "approvals" ? reviewTasks.length : item.count}</span> : null}
              </button>
            );
          })}
        </nav>

        <div className="nav-label nav-label--spaced">Tools</div>
        <nav className="primary-nav">
          <button className="nav-item"><Bot size={17} strokeWidth={1.8} /><span>Agent skills</span><ArrowUpRight size={13} className="external-icon" /></button>
          <button className="nav-item"><LockKeyhole size={17} strokeWidth={1.8} /><span>Security policy</span></button>
          <button className="nav-item"><Settings2 size={17} strokeWidth={1.8} /><span>Settings</span></button>
        </nav>

        <div className="sidebar-footer">
          <div className="sandbox-card">
            <div className="sandbox-topline"><span className="pulse-dot" /> Sandbox ready <span className="sandbox-latency">42ms</span></div>
            <div className="sandbox-detail">Docker · restricted network</div>
          </div>
          <div className="user-row">
            <div className="avatar">LC</div>
            <div className="user-copy"><strong>Lin Chen</strong><span>Owner</span></div>
            <MoreHorizontal size={16} />
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button menu-toggle" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={18} /></button>
            <div className="breadcrumbs"><span>Workspace</span><span className="breadcrumb-slash">/</span><strong>{pageTitle}</strong></div>
          </div>
          <div className="topbar-actions">
            <div className={`connection-status connection-status--${apiStatus}`}><span className="pulse-dot" /> {apiStatus === "connected" ? "API connected" : apiStatus === "offline" ? "API offline" : "Checking API"}</div>
            <button className="icon-button" aria-label="Search"><Search size={17} /></button>
            <button className="icon-button" aria-label="Command menu"><Command size={17} /></button>
            <div className="topbar-avatar">LC</div>
          </div>
        </header>

        <div className="page-wrap">
          <section className="hero-block">
            <div className="hero-copy">
              <div className="eyebrow"><span className="eyebrow-line" /> CONTROL ROOM / MONDAY 05 OCT</div>
              <h1>Build with <em>intent.</em></h1>
              <p>One workspace for the full engineering loop — from issue signal to tested, reviewable change.</p>
            </div>
            <div className="hero-meta">
              <div className="hero-meta-label">Current focus</div>
              <div className="focus-card"><div className="focus-icon"><Activity size={17} /></div><div><strong>{activeTask?.title ?? "No active task"}</strong><span>{activeTask ? `${activeTask.repo} · ${activeTask.branch}` : "Create a task to begin"}</span></div>{activeTask ? <span className="status-chip status-chip--running">LIVE</span> : null}</div>
            </div>
          </section>

          <section className="metric-grid" aria-label="Workspace metrics">
            <MetricCard label="Active tasks" value={String(workspaceSummary?.active_tasks ?? tasks.filter((task) => task.status === "running").length).padStart(2, "0")} delta="Live from API" icon={Zap} tone="amber" />
            <MetricCard label="Repositories" value={String(repositories.length).padStart(2, "0")} delta={repositories.length ? "Connected locally" : "Add a repository"} icon={GitBranch} tone="cyan" />
            <MetricCard label="Success rate" value="—" delta="No completed runs" icon={Activity} tone="lime" />
            <MetricCard label="Needs review" value={String(workspaceSummary?.pending_approvals ?? reviewTasks.length).padStart(2, "0")} delta="Human in loop" icon={ShieldCheck} tone="rose" />
          </section>

          <section className="command-panel">
            <div className="section-heading"><div><div className="section-kicker"><Sparkles size={13} /> START A TASK</div><h2>What should Forge work on?</h2></div><span className="shortcut-hint"><Command size={12} /> K</span></div>
            <div className="prompt-row">
              <div className="prompt-prefix"><span className="prompt-dot" /><span>forge</span><span className="prompt-arrow">›</span></div>
              <input value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") runTask(); }} placeholder="Describe an issue, a change, or a question about your codebase..." aria-label="Task prompt" />
              <button className="run-button" onClick={() => void runTask()} disabled={!prompt.trim() || isRunning}><span>{isRunning ? "Running" : "Run task"}</span>{isRunning ? <Activity size={15} className="spin" /> : <Send size={15} />}</button>
            </div>
            <div className="prompt-footer"><span><CircleDot size={12} /> Agent will inspect the current workspace</span><span className="prompt-footer-right"><LockKeyhole size={12} /> Sandbox enforced</span></div>{taskError ? <div className="repo-error task-error">{taskError}</div> : null}
          </section>

          <div className="content-grid">
            <section className="panel task-panel">
              <div className="panel-header"><div><div className="section-kicker">IN FLIGHT</div><h2>Task queue</h2></div><button className="text-button" onClick={() => setActiveNav("tasks")}>View all <ArrowUpRight size={14} /></button></div>
              <div className="task-list">
                {tasks.slice(0, 4).map((task) => <TaskRow key={task.id} task={task} active={task.id === activeTaskId} onClick={() => setActiveTaskId(task.id)} />)}
                {!tasks.length ? <div className="empty-operation queue-empty"><Zap size={17} /><span>No tasks yet. Start with a small, specific change.</span></div> : null}
              </div>
              <button className="add-task-button" onClick={() => document.querySelector<HTMLInputElement>(".prompt-row input")?.focus()}><Plus size={15} /> Start another task</button>
            </section>

            <section className="panel stream-panel">
              <div className="panel-header"><div><div className="section-kicker">TRACE / {activeTask?.id.toUpperCase()}</div><h2>Execution stream</h2></div><span className="live-badge"><span className="pulse-dot" /> Live</span></div>
              {activeTask ? <div className="iteration-bar"><div className="iteration-status"><strong>Iteration {activeTask.currentIteration || iterations.length || 0}</strong><span>{activeTask.nextAction ?? activeTask.plan?.objective ?? "Current coding loop"}</span></div><div className="iteration-actions">{activeTask.status === "failed" ? <button className="small-action" onClick={() => void beginRepair()} disabled={iterationRunning}>Repair</button> : activeTask.status === "paused" ? <button className="small-action" onClick={() => void changeTaskState("resume")} disabled={iterationRunning}>Resume</button> : activeTask.status === "review" && !iterations.length ? planApproval?.status === "approved" ? <button className="small-action" onClick={() => void beginIteration()} disabled={iterationRunning}>Start iteration</button> : <button className="small-action" onClick={() => void requestPlanApproval()} disabled={iterationRunning}>{planApproval ? "Awaiting approval" : "Request plan approval"}</button> : null}{planApproval?.status === "pending" ? <button className="small-action small-action--muted" onClick={() => void approvePlan()} disabled={iterationRunning}>Approve plan</button> : null}{["coding", "testing", "running"].includes(activeTask.status) ? <button className="small-action small-action--muted" onClick={() => void changeTaskState("pause")} disabled={iterationRunning}>Pause</button> : null}{!["cancelled", "done"].includes(activeTask.status) ? <button className="text-button" onClick={() => void changeTaskState("cancel")} disabled={iterationRunning}>Cancel</button> : null}</div></div> : null}
              {iterationError ? <div className="inline-error">{iterationError}</div> : null}
              {activeTask?.plan ? <div className="plan-list"><div className="plan-label">PLAN / {activeTask.plan.steps.length} STEPS</div>{activeTask.plan.steps.map((step, index) => <div className="plan-step" key={step.id}><span className={`plan-step-index ${index === 0 ? "plan-step-index--active" : ""}`}>{index + 1}</span><div className="plan-step-copy"><strong>{step.title}</strong><span>{step.description}</span></div><span className={`plan-risk plan-risk--${step.risk}`}>{step.risk}</span></div>)}</div> : null}
              {iterations.length ? <div className="iteration-history"><div className="plan-label">ITERATIONS / {iterations.length}</div>{iterations.map((iteration) => <div className="iteration-row" key={iteration.id}><span className={`iteration-dot iteration-dot--${iteration.status}`} /><div className="iteration-row-copy"><strong>#{iteration.number} {iteration.status}</strong><span>{iteration.goal}</span>{iteration.failure_summary ? <small>{iteration.failure_summary}</small> : null}</div>{iteration.test_result?.failed_tests?.[0] ? <span className="failure-location">{iteration.test_result.failed_tests[0].path ?? "test output"}{iteration.test_result.failed_tests[0].line ? `:${iteration.test_result.failed_tests[0].line}` : ""}</span> : null}</div>)}</div> : null}
              <div className="stream-list">
                {displayedStreamEvents.map((event) => {
                  const Icon = iconForEvent(event.icon);
                  return <div className={`stream-event stream-event--${event.status}`} key={event.title}><div className="stream-time">{event.time}</div><div className="stream-marker"><Icon size={14} /></div><div className="stream-copy"><strong>{event.title}</strong><span>{event.detail}</span></div>{event.status === "done" ? <Check size={15} className="stream-check" /> : event.status === "active" ? <span className="stream-spinner" /> : <Clock3 size={15} className="stream-wait" />}</div>;
                })}
                {!displayedStreamEvents.length ? <div className="empty-operation"><Activity size={17} /><span>Select or create a task to see its execution events.</span></div> : null}
              </div>
              {eventsError ? <div className="inline-error">{eventsError}</div> : null}
              <div className="stream-actions">
                <button className="console-link" onClick={() => setActiveNav("runs")}><TerminalSquare size={14} /> Open full run console <ArrowUpRight size={13} /></button>
                <span className="event-source">{runEvents.length ? `${runEvents.length} recorded events` : "Waiting for recorded events"}</span>
              </div>
            </section>
          </div>

          <section className="operations-grid">
            <section className="panel test-panel">
              <div className="panel-header"><div><div className="section-kicker"><PackageCheck size={13} /> VALIDATION</div><h2>Test console</h2></div><button className="small-action" onClick={() => void executeTests()} disabled={!activeTask?.runId || testRunning}>{testRunning ? <><Activity size={13} className="spin" /> Running</> : <><TerminalSquare size={13} /> Run tests</>}</button></div>
              {testResult ? <div className="test-result">
                <div className="test-result-top"><span className={`result-badge result-badge--${testResult.status}`}>{testResult.status.replace("_", " ")}</span><span>{testResult.duration_ms} ms · exit {testResult.exit_code ?? "—"}</span></div>
                <code className="test-command">{testResult.command.length ? testResult.command.join(" ") : "No command detected"}</code>
                {testResult.stdout || testResult.stderr ? <pre className="test-output">{testResult.stdout || testResult.stderr}</pre> : <span className="empty-detail">No output returned.</span>}
                {testResult.output_truncated ? <span className="output-note">Output truncated at the API limit.</span> : null}
              </div> : <div className="empty-operation"><TerminalSquare size={17} /><span>Run the detected project test command for this task.</span></div>}
              {testError ? <div className="inline-error">{testError}</div> : null}
            </section>

            <section className="panel patch-panel">
              <div className="panel-header"><div><div className="section-kicker"><FileCode2 size={13} /> CHANGE REVIEW</div><h2>Patch preview</h2></div><span className="patch-safety"><LockKeyhole size={12} /> checkpointed</span></div>
              <div className="patch-form">
                <input value={patchPath} onChange={(event) => setPatchPath(event.target.value)} placeholder="relative/path.py" aria-label="Patch file path" />
                <textarea value={patchContent} onChange={(event) => setPatchContent(event.target.value)} placeholder="Paste the complete file content to preview a low risk update" aria-label="Patch file content" rows={4} />
                <div className="patch-form-actions"><span>Update only · workspace paths are enforced</span><button className="small-action" onClick={() => void previewActivePatch()} disabled={!activeTask?.runId || !patchPath.trim() || patchRunning}>{patchRunning ? "Working" : "Preview diff"}</button></div>
              </div>
              {patchPreviewResult ? <div className="patch-result"><div className="patch-summary"><span>{patchPreviewResult.files.length} file</span><span className="diff-add">+{patchPreviewResult.additions}</span><span className="diff-del">-{patchPreviewResult.deletions}</span><span>{patchPreviewResult.bytes_changed} bytes</span></div><pre className="diff-view">{patchPreviewResult.diff || "No changes detected."}</pre><div className="patch-result-actions"><span>Review the unified diff before applying.</span><div className="patch-result-buttons"><button className="small-action small-action--muted" onClick={cancelPatchPreview} disabled={patchRunning}>Cancel</button><button className="small-action" onClick={() => void applyActivePatch()} disabled={patchRunning || !patchPreviewResult.diff}>Apply and checkpoint</button></div></div></div> : <div className="empty-operation"><FileCode2 size={17} /><span>Preview a complete file update before it touches the repository.</span></div>}
              {patchApplied ? <div className="success-note"><Check size={14} /> {patchApplied}</div> : null}
              {patchError ? <div className="inline-error">{patchError}</div> : null}
            </section>
          </section>

          <section className="operations-grid git-grid">
            <section className="panel git-panel">
              <div className="panel-header"><div><div className="section-kicker"><GitBranch size={13} /> BRANCH ISOLATION</div><h2>Task branch</h2></div>{gitSnapshot ? <span className="patch-safety">{gitSnapshot.clean ? "clean" : `${gitSnapshot.changed_files.length} changed`}</span> : null}</div>
              {gitSnapshot ? <div className="git-snapshot"><div><span>Branch</span><strong>{gitSnapshot.branch}</strong></div><div><span>HEAD</span><code>{gitSnapshot.head.slice(0, 10)}</code></div></div> : <div className="empty-operation"><GitBranch size={17} /><span>Create a task branch before preparing a commit.</span></div>}
              <div className="git-form"><input value={branchName} onChange={(event) => setBranchName(event.target.value)} placeholder="feat/task-short-name" aria-label="Task branch name" /><button className="small-action" onClick={() => void createBranch()} disabled={!activeTask || !branchName.trim() || gitRunning}><GitBranch size={13} /> Create branch</button></div>
            </section>
            <section className="panel git-panel">
              <div className="panel-header"><div><div className="section-kicker"><GitPullRequest size={13} /> COMMIT REVIEW</div><h2>Commit preview</h2></div><span className="patch-safety"><LockKeyhole size={12} /> local only</span></div>
              <div className="git-form"><input value={commitMessage} onChange={(event) => setCommitMessage(event.target.value)} placeholder="Explain the change in one line" aria-label="Commit message" /><button className="small-action" onClick={() => void previewTaskCommit()} disabled={!activeTask || !commitMessage.trim() || gitRunning}>Preview commit</button></div>
              {commitPreviewResult ? <div className="patch-result"><div className="patch-summary"><span>{commitPreviewResult.changed_files.length} files</span><span>{commitPreviewResult.branch}</span><span>{commitPreviewResult.ready ? "ready" : "empty"}</span></div><pre className="diff-view">{commitPreviewResult.diff || "No uncommitted changes."}</pre><div className="patch-result-actions"><span>Tests must pass before commit.</span><div className="patch-result-buttons">{writeApproval?.status === "approved" ? <button className="small-action" onClick={() => void commitTask()} disabled={!commitPreviewResult.ready || gitRunning}>Create commit</button> : <><button className="small-action small-action--muted" onClick={() => void requestWriteApproval()} disabled={!commitPreviewResult.ready || gitRunning}>{writeApproval ? "Awaiting approval" : "Request write approval"}</button>{writeApproval?.status === "pending" ? <button className="small-action" onClick={() => void approveWrite()} disabled={gitRunning}>Approve</button> : null}</>}</div></div></div> : <div className="empty-operation"><GitPullRequest size={17} /><span>Preview the local diff before creating the task commit.</span></div>}
              {gitError ? <div className="inline-error">{gitError}</div> : null}
            </section>
          </section>

          <section className="lower-grid">
            <section className="panel repo-panel"><div className="panel-header"><div><div className="section-kicker">CODEBASE PULSE</div><h2>Repositories</h2></div><button className="small-action" onClick={() => void addRepository()}><Plus size={13} /> Add</button></div>{repositories.length ? repositories.map((repository, index) => <RepoRow key={repository.id} repository={repository} tone={["amber", "cyan", "lime"][index % 3]} />) : <div className="repo-empty"><GitBranch size={18} /><span>No local repository connected yet.</span><button className="text-button" onClick={() => void addRepository()}>Add a repository <ArrowUpRight size={13} /></button></div>}{repositoryError ? <div className="repo-error">{repositoryError}</div> : null}</section>
            <section className="panel approval-panel"><div className="panel-header"><div><div className="section-kicker">HUMAN IN THE LOOP</div><h2>Needs your call</h2></div><span className="approval-count">{String(reviewTasks.length).padStart(2, "0")}</span></div>{reviewTasks.length ? reviewTasks.slice(0, 3).map((task) => <div className="approval-item" key={task.id}><div className="approval-icon approval-icon--rose"><ShieldCheck size={16} /></div><div className="approval-copy"><strong>Review task plan</strong><span>{task.id} · {task.title}</span></div><button className="small-action" onClick={() => setActiveTaskId(task.id)}>Review</button></div>) : <div className="empty-operation"><ShieldCheck size={17} /><span>No tasks are waiting for approval.</span></div>}</section>
          </section>

          <footer className="page-footer"><span><span className="footer-mark" /> Forge is running locally</span><span>Last synced just now · v0.1 foundation</span></footer>
        </div>
      </main>
    </div>
  );
}

function MetricCard({ label, value, delta, icon: Icon, tone }: { label: string; value: string; delta: string; icon: typeof Activity; tone: string }) {
  return <div className={`metric-card metric-card--${tone}`}><div className="metric-top"><span>{label}</span><span className="metric-icon"><Icon size={15} /></span></div><div className="metric-value">{value}</div><div className="metric-delta"><span className="delta-line" />{delta}</div></div>;
}

function TaskRow({ task, active, onClick }: { task: Task; active: boolean; onClick: () => void }) {
  return <button className={`task-row ${active ? "task-row--active" : ""}`} onClick={onClick}><span className={`task-accent task-accent--${task.accent}`} /><span className="task-main"><span className="task-title">{task.title}</span><span className="task-meta"><span>{task.repo}</span><span className="meta-separator">·</span><span>{task.branch}</span></span></span><span className={`task-status task-status--${task.status}`}>{task.status === "running" ? <><span className="pulse-dot" />Running</> : task.status === "review" ? "Review" : task.status === "failed" ? "Failed" : task.status === "done" ? "Done" : "Queued"}</span><span className="task-time">{task.time}</span></button>;
}

function RepoRow({ repository, tone }: { repository: Repository; tone: string }) {
  const language = Object.keys(repository.languages)[0] ?? "Unknown";
  const status = repository.changed_files ? `${repository.changed_files} changes` : "Synced";
  return <div className="repo-row"><div className={`repo-icon repo-icon--${tone}`}><Box size={15} /></div><div className="repo-copy"><strong>{repository.name}</strong><span>{language} · {repository.branch}</span></div><span className={`repo-status repo-status--${tone}`}><span className="mini-dot" />{status}</span></div>;
}

export default App;
