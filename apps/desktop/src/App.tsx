import { useEffect, useMemo, useRef, useState } from "react";
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
  Languages,
  LockKeyhole,
  Menu,
  Monitor,
  MoreHorizontal,
  PackageCheck,
  Plus,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Sun,
  Moon,
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
  recoverTask,
  type AgentTask,
  type AuditEvent,
  type CommitPreview,
  type GitSnapshot,
  type Approval,
  type PatchPreview,
  type Repository,
  type TestRunResult,
  type TaskIteration,
  type TestKind,
  type SourceFile,
  type PatchFileInput,
  type WorkspaceSummary,
} from "./lib/api";
import { usePreferences, type Language, type ThemeMode } from "./lib/preferences";

import { Dialog } from "@coding-agent/ui";
import { RepositoryBrowser } from "./components/RepositoryBrowser";
import { RunHistory } from "./components/RunHistory";

type NavKey = "overview" | "tasks" | "repositories" | "runs" | "approvals";

type Task = {
  id: string;
  runId?: string;
  repositoryId?: string | null;
  lifecycleStatus: AgentTask["status"];
  taskBranch: string | null;
  currentIteration: number;
  retryCount: number;
  nextAction: string | null;
  requiresRecovery: boolean;
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
    lifecycleStatus: task.status,
    taskBranch: task.task_branch,
    currentIteration: task.current_iteration,
    retryCount: task.retry_count,
    nextAction: task.next_action,
    requiresRecovery: task.requires_recovery,
    title: task.goal,
    repo: task.plan?.repository_summary ?? "workspace",
    branch: task.branch ?? task.run_id,
    status: task.status === "planning" || task.status === "queued" ? "queued" : task.status === "failed" ? "failed" : task.status === "cancelled" ? "cancelled" : task.status === "paused" ? "paused" : task.status === "done" || task.status === "completed" || task.status === "pr_created" ? "done" : task.status === "review" || task.status === "awaiting_approval" || task.status === "ready_for_pr" ? "review" : "running",
    time: "now",
    accent: task.status === "failed" ? "rose" : "amber",
    plan: task.plan,
  };
}

const navItems: Array<{ key: NavKey; icon: typeof LayoutDashboard; count?: number }> = [
  { key: "overview", icon: LayoutDashboard },
  { key: "tasks", icon: Zap, count: 4 },
  { key: "repositories", icon: GitBranch },
  { key: "runs", icon: TerminalSquare },
  { key: "approvals", icon: ShieldCheck, count: 2 },
];

function eventToStreamEvent(event: AuditEvent, t: (key: string) => string, locale: Language): StreamEvent {
  const action = event.action.toLowerCase();
  const icon: StreamEvent["icon"] = action.includes("test") ? "test" : action.includes("patch") ? "code" : action.includes("repository") || action.includes("search") ? "search" : "approve";
  const title = action === "task.create" ? t("taskCreatedEvent") : action === "patch.preview" ? t("patchPreviewEvent") : action === "patch.apply" ? t("patchAppliedEvent") : action === "tests.run" ? t("testsCompletedEvent") : action === "checkpoint.rollback" ? t("checkpointRestoredEvent") : event.action;
  const details = event.details;
  const command = Array.isArray(details.command) ? details.command.join(" ") : undefined;
  const detail = command ? `${event.summary} · ${command}` : event.summary;
  return {
    time: new Date(event.created_at).toLocaleTimeString(locale, { hour12: false }),
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
  const { language, theme, setLanguage, setTheme, t } = usePreferences();
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
  const [selectedRepositoryId, setSelectedRepositoryId] = useState("");
  const [sourceLocation, setSourceLocation] = useState<{ path: string; line: number } | null>(null);
  const [taskFilter, setTaskFilter] = useState("all");
  const [testKind, setTestKind] = useState<TestKind>("auto");
  const [testTarget, setTestTarget] = useState("");
  const [patchExpectedHash, setPatchExpectedHash] = useState<string>();
  const [patchOperation, setPatchOperation] = useState<PatchFileInput["operation"]>("update");
  const [repositoryDialogOpen, setRepositoryDialogOpen] = useState(false);
  const [repositoryPathInput, setRepositoryPathInput] = useState("");
  const [repositorySaving, setRepositorySaving] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"cancel" | "delete" | null>(null);
  const taskRef = useRef(activeTaskId);
  useEffect(() => { taskRef.current = activeTaskId; }, [activeTaskId]);
  useEffect(() => { if (!repositories.some(repository => repository.id === selectedRepositoryId)) setSelectedRepositoryId(repositories[0]?.id ?? ""); }, [repositories, selectedRepositoryId]);

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
        if (!disposed) setEventsError(error instanceof Error ? error.message : t("eventsError"));
      }
    };
    void loadEvents();
    const timer = window.setInterval(() => void loadEvents(), 4000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [activeTask?.runId, t]);

  useEffect(() => {
    setTestResult(null);
    setTestError(null);
    setPatchPreviewResult(null);
    setPatchApplied(null);
    setPatchError(null);
    setPatchPath("");
    setPatchContent("");
    setPatchExpectedHash(undefined);
    setPatchOperation("update");
    setCommitPreviewResult(null);
    setCommitMessage("");
    setTestTarget("");
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
    listIterations(activeTaskId, controller.signal).then((payload) => { setIterations(payload.items); setTestResult(payload.items.at(-1)?.test_result ?? null); }).catch(() => setIterations([]));
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

  const pageTitle = t(`nav.${activeNav}`);
  const displayedStreamEvents = runEvents.map((event) => eventToStreamEvent(event, t, language));
  const reviewTasks = tasks.filter((task) => task.status === "review");
  const planApproval = approvals.find((item) => item.type === "plan");
  const writeApproval = approvals.find((item) => item.type === "write" && item.scope_hash === commitPreviewResult?.scope_hash);

  const runTask = async () => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt || isRunning) return;
    setTaskError(null);
    setIsRunning(true);
    setPrompt("");
    try {
      const task = await createTask(trimmedPrompt, selectedRepositoryId || repositories[0]?.id);
      const newTask = taskFromApi(task);
      setTasks((current) => [newTask, ...current.filter((item) => item.id !== newTask.id)]);
      setActiveTaskId(newTask.id);
    } catch (error) {
      setTaskError(error instanceof Error ? error.message : t("taskCreatedError"));
      setPrompt(trimmedPrompt);
    } finally {
      setIsRunning(false);
    }
  };

  const addRepository = () => {
    setRepositoryDialogOpen(true); setRepositoryError(null);
  };

  const saveRepository = async () => {
    if (!repositoryPathInput.trim() || repositorySaving) return;
    setRepositorySaving(true); setRepositoryError(null);
    try {
      const repository = await registerRepository(repositoryPathInput.trim());
      setRepositories(current => [repository, ...current.filter(item => item.id !== repository.id)]);
      setSelectedRepositoryId(repository.id); setRepositoryDialogOpen(false); setRepositoryPathInput("");
      setWorkspaceSummary(await getWorkspaceSummary());
    } catch (error) { setRepositoryError(error instanceof Error ? error.message : t("repositoryError")); }
    finally { setRepositorySaving(false); }
  };

  const executeTests = async () => {
    if (!activeTask?.runId || testRunning) return;
    setTestRunning(true);
    setTestError(null);
    try {
      const result = await runTests(activeTask.runId, testKind, testTarget.trim());
      if (taskRef.current !== activeTask.id) return;
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
      setTestError(error instanceof Error ? error.message : t("testsError"));
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
      const preview = await previewPatch(activeTask.runId, [{ path: patchPath.trim(), content: patchOperation === "delete" ? undefined : patchContent, operation: patchOperation, expected_hash: patchExpectedHash }]);
      setPatchPreviewResult(preview);
      const events = await listRunEvents(activeTask.runId);
      setRunEvents(events.items);
    } catch (error) {
      setPatchError(error instanceof Error ? error.message : t("patchPreviewError"));
    } finally {
      setPatchRunning(false);
    }
  };

  const applyActivePatch = async (confirmedDelete = false) => {
    if (!activeTask?.runId || !patchPreviewResult || patchRunning) return;
    if (patchPreviewResult.requires_delete_confirmation && !confirmedDelete) { setConfirmAction("delete"); return; }
    setConfirmAction(null);
    setPatchRunning(true);
    setPatchError(null);
    try {
      const result = await applyPatch(activeTask.runId, patchPreviewResult.patch_id, true);
      setPatchApplied(t("patchAppliedSummary").replace("{checkpoint}", result.checkpoint.id).replace("{files}", String(result.applied_files.length)));
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
      setPatchError(error instanceof Error ? error.message : t("patchApplyError"));
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
      setGitError(error instanceof Error ? error.message : t("branchError"));
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
      setGitError(error instanceof Error ? error.message : t("commitPreviewError"));
    } finally {
      setGitRunning(false);
    }
  };

  const commitTask = async () => {
    if (!activeTask || !commitPreviewResult?.ready || gitRunning) return;
    setGitRunning(true);
    setGitError(null);
    try {
      const snapshot = await createCommit(activeTask.id, commitMessage.trim(), commitPreviewResult.scope_hash);
      setGitSnapshot(snapshot);
      setCommitPreviewResult(null);
      const task = await getTask(activeTask.id);
      updateTaskFromApi(task);
    } catch (error) {
      setGitError(error instanceof Error ? error.message : t("commitError"));
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
      setGitError(error instanceof Error ? error.message : t("writeApprovalRequestError"));
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
      setGitError(error instanceof Error ? error.message : t("writeApprovalDecisionError"));
    } finally {
      setGitRunning(false);
    }
  };

  const updateTaskFromApi = (task: AgentTask) => {
    const nextTask = taskFromApi(task);
    setTasks((current) => [nextTask, ...current.filter((item) => item.id !== nextTask.id)]);
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
      setIterationError(error instanceof Error ? error.message : t("iterationStartError"));
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
      setIterationError(error instanceof Error ? error.message : t("approvalRequestError"));
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
      setIterationError(error instanceof Error ? error.message : t("approvalDecisionError"));
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
      setIterationError(error instanceof Error ? error.message : t("repairStartError"));
    } finally {
      setIterationRunning(false);
    }
  };

  const changeTaskState = async (action: "pause" | "resume" | "cancel", confirmed = false) => {
    if (!activeTask || iterationRunning) return;
    if (action === "cancel" && !confirmed) { setConfirmAction("cancel"); return; }
    setConfirmAction(null);
    setIterationRunning(true);
    setIterationError(null);
    try {
      const result = action === "pause" ? await pauseTask(activeTask.id) : action === "resume" ? await resumeTask(activeTask.id) : await cancelTask(activeTask.id);
      updateTaskFromApi(result.task);
    } catch (error) {
      setIterationError(error instanceof Error ? error.message : t("taskStateError"));
    } finally {
      setIterationRunning(false);
    }
  };

  const confirmRecovery = async () => {
    if (!activeTask || iterationRunning) return;
    setIterationRunning(true);
    setIterationError(null);
    try {
      updateTaskFromApi((await recoverTask(activeTask.id)).task);
    } catch (error) {
      setIterationError(error instanceof Error ? error.message : t("taskStateError"));
    } finally {
      setIterationRunning(false);
    }
  };

  const refreshActiveTask = async () => {
    if (!activeTask?.runId) return;
    const [task, nextIterations, events, repositoryList, snapshot, summary, approvalList] = await Promise.all([
      getTask(activeTask.id), listIterations(activeTask.id), listRunEvents(activeTask.runId), listRepositories(),
      activeTask.repositoryId ? getTaskGitSnapshot(activeTask.id) : Promise.resolve(null), getWorkspaceSummary(), listApprovals(activeTask.id),
    ]);
    updateTaskFromApi(task); setIterations(nextIterations.items); setRunEvents(events.items);
    setRepositories(repositoryList.items); setGitSnapshot(snapshot); setWorkspaceSummary(summary); setApprovals(approvalList.items);
    setTestResult(nextIterations.items.at(-1)?.test_result ?? null);
    setPatchPreviewResult(null); setPatchApplied(null); setCommitPreviewResult(null); setPatchExpectedHash(undefined);
  };

  const prepareSourceChange = (file: SourceFile) => {
    setPatchPath(file.path); setPatchContent(file.content); setPatchExpectedHash(file.content_hash);
    setPatchOperation("update"); setPatchPreviewResult(null); setPatchApplied(null); setPatchError(null); setActiveNav("runs");
  };

  const openFailure = (path: string, line: number) => {
    if (!activeTask?.repositoryId) return;
    setSelectedRepositoryId(activeTask.repositoryId); setSourceLocation({ path, line }); setActiveNav("repositories");
  };

  const filteredTasks = tasks.filter(task => taskFilter === "all" || task.status === taskFilter);

  return (
    <div className="app-shell">
      <Dialog open={repositoryDialogOpen} title={t("addGitRepository")} onClose={() => { if (!repositorySaving) setRepositoryDialogOpen(false); }}>
        <form onSubmit={event => { event.preventDefault(); void saveRepository(); }}>
          <label className="dialog-label" htmlFor="repository-path">{t("repositoryPathPrompt")}</label>
          <input id="repository-path" autoFocus value={repositoryPathInput} onChange={event => setRepositoryPathInput(event.target.value)} disabled={repositorySaving} />
          {repositoryError ? <p className="inline-error" role="alert">{repositoryError}</p> : null}
          <div className="dialog-actions"><button type="button" className="small-action small-action--muted" disabled={repositorySaving} onClick={() => setRepositoryDialogOpen(false)}>{t("cancel")}</button><button className="small-action" disabled={repositorySaving || !repositoryPathInput.trim()}>{repositorySaving ? t("common.loading") : t("add")}</button></div>
        </form>
      </Dialog>
      <Dialog open={confirmAction !== null} title={confirmAction === "delete" ? t("patch.delete") : t("cancel")} onClose={() => setConfirmAction(null)}>
        <p>{confirmAction === "delete" ? t("patch.deleteConfirm") : t("cancelTaskConfirm")}</p>
        {confirmAction === "delete" ? <pre>{patchPreviewResult?.files.join("\n")}</pre> : null}
        <div className="dialog-actions"><button className="small-action small-action--muted" onClick={() => setConfirmAction(null)}>{t("common.back")}</button><button className="small-action" onClick={() => confirmAction === "delete" ? void applyActivePatch(true) : void changeTaskState("cancel", true)}>{t("common.confirm")}</button></div>
      </Dialog>
      <aside className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark">F</div>
          <div>
            <div className="brand-name">FORGE</div>
            <div className="brand-subtitle">coding agent / 0.1</div>
          </div>
          <button className="icon-button mobile-close" aria-label={t("closeNavigation")} onClick={() => setSidebarOpen(false)}>
            <X size={17} />
          </button>
        </div>

          <button className="workspace-switcher" onClick={() => void addRepository()} title={t("addGitRepository")}>
          <span className="workspace-icon"><Code2 size={15} /></span>
          <span className="workspace-copy">
            <strong>{repositories[0]?.name ?? t("noRepository")}</strong>
            <small>{repositories[0] ? t("localWorkspace") : t("addGitRepository")}</small>
          </span>
          <ChevronDown size={15} />
        </button>

        <div className="nav-label">{t("workspace")}</div>
        <nav className="primary-nav" aria-label={t("primaryNavigation")}>
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
                <span>{t(`nav.${item.key}`)}</span>
                {(item.key === "tasks" ? tasks.length : item.key === "approvals" ? reviewTasks.length : item.count) ? <span className="nav-count">{item.key === "tasks" ? tasks.length : item.key === "approvals" ? reviewTasks.length : item.count}</span> : null}
              </button>
            );
          })}
        </nav>

        <div className="nav-label nav-label--spaced">{t("tools")}</div>
        <nav className="primary-nav">
          <button className="nav-item"><Bot size={17} strokeWidth={1.8} /><span>{t("agentSkills")}</span><ArrowUpRight size={13} className="external-icon" /></button>
          <button className="nav-item"><LockKeyhole size={17} strokeWidth={1.8} /><span>{t("securityPolicy")}</span></button>
          <button className="nav-item"><Settings2 size={17} strokeWidth={1.8} /><span>{t("settings")}</span></button>
        </nav>

        <div className="sidebar-footer">
          <div className="sandbox-card">
            <div className="sandbox-topline"><span className="pulse-dot" /> {t("localExecution")}</div>
            <div className="sandbox-detail">{t("localExecutionDetail")}</div>
          </div>
          <div className="user-row">
            <div className="avatar">LC</div>
            <div className="user-copy"><strong>Lin Chen</strong><span>{t("owner")}</span></div>
            <MoreHorizontal size={16} />
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="topbar-left">
          <button className="icon-button menu-toggle" aria-label={t("openNavigation")} onClick={() => setSidebarOpen(true)}><Menu size={18} /></button>
            <div className="breadcrumbs"><span>{t("workspace")}</span><span className="breadcrumb-slash">/</span><strong>{pageTitle}</strong></div>
          </div>
          <div className="topbar-actions">
            <div className={`connection-status connection-status--${apiStatus}`}><span className="pulse-dot" /> {apiStatus === "connected" ? t("apiConnected") : apiStatus === "offline" ? t("apiOffline") : t("checkingApi")}</div>
            <div className="preference-controls" aria-label={t("preferences")}>
              <label className="preference-control"><Languages size={14} /><span className="sr-only">{t("language")}</span><select value={language} onChange={(event) => setLanguage(event.target.value as Language)} aria-label={t("language")}><option value="zh-CN">{t("chinese")}</option><option value="en-US">{t("english")}</option></select></label>
              <label className="preference-control"><span className="preference-theme-icon">{theme === "light" ? <Sun size={14} /> : theme === "dark" ? <Moon size={14} /> : <Monitor size={14} />}</span><span className="sr-only">{t("theme")}</span><select value={theme} onChange={(event) => setTheme(event.target.value as ThemeMode)} aria-label={t("theme")}><option value="system">{t("system")}</option><option value="light">{t("light")}</option><option value="dark">{t("dark")}</option></select></label>
            </div>
            <button className="icon-button" aria-label={t("search")} title={t("search")}><Search size={17} /></button>
            <button className="icon-button" aria-label={t("commandMenu")} title={t("commandMenu")}><Command size={17} /></button>
            <div className="topbar-avatar">LC</div>
          </div>
        </header>

        <div className="page-wrap" data-view={activeNav}>
          {activeNav === "repositories" ? <RepositoryBrowser repositories={repositories} repositoryId={selectedRepositoryId} onRepositoryChange={(id) => { setSelectedRepositoryId(id); setSourceLocation(null); }} onEdit={prepareSourceChange} canEdit={Boolean(activeTask?.repositoryId === selectedRepositoryId && activeTask.lifecycleStatus === "coding")} initialFile={sourceLocation} /> : null}
          <section className="hero-block">
            <div className="hero-copy">
              <div className="eyebrow"><span className="eyebrow-line" /> {t("controlRoom")} / {new Intl.DateTimeFormat(language, { weekday: "long", day: "2-digit", month: "short" }).format(new Date()).toUpperCase()}</div>
              <h1>{t("buildWithIntent")}</h1>
              <p>{t("engineeringLoop")}</p>
            </div>
            <div className="hero-meta">
              <div className="hero-meta-label">{t("currentFocus")}</div>
              <div className="focus-card"><div className="focus-icon"><Activity size={17} /></div><div><strong>{activeTask?.title ?? t("noActiveTask")}</strong><span>{activeTask ? `${activeTask.repo} · ${activeTask.branch}` : t("createTaskToBegin")}</span></div>{activeTask ? <span className="status-chip status-chip--running">{t("live")}</span> : null}</div>
            </div>
          </section>

          <section className="metric-grid" aria-label="Workspace metrics">
            <MetricCard label={t("activeTasks")} value={String(workspaceSummary?.active_tasks ?? tasks.filter((task) => task.status === "running").length).padStart(2, "0")} delta={t("liveFromApi")} icon={Zap} tone="amber" />
            <MetricCard label={t("repositories")} value={String(repositories.length).padStart(2, "0")} delta={repositories.length ? t("connectedLocally") : t("addRepository")} icon={GitBranch} tone="cyan" />
            <MetricCard label={t("successRate")} value="—" delta={t("noCompletedRuns")} icon={Activity} tone="lime" />
            <MetricCard label={t("needsReview")} value={String(workspaceSummary?.pending_approvals ?? reviewTasks.length).padStart(2, "0")} delta={t("humanInLoop")} icon={ShieldCheck} tone="rose" />
          </section>

          <section className="command-panel">
            <div className="section-heading"><div><div className="section-kicker"><Sparkles size={13} /> {t("startTask")}</div><h2>{t("whatShouldForgeWorkOn")}</h2></div><span className="shortcut-hint"><Command size={12} /> K</span></div>
            <div className="prompt-row">
              <div className="prompt-prefix"><span className="prompt-dot" /><span>forge</span><span className="prompt-arrow">›</span></div>
              <input value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") runTask(); }} placeholder={t("taskPromptPlaceholder")} aria-label={t("taskPromptLabel")} />
              <button className="run-button" onClick={() => void runTask()} disabled={!prompt.trim() || isRunning}><span>{isRunning ? t("running") : t("runTask")}</span>{isRunning ? <Activity size={15} className="spin" /> : <Send size={15} />}</button>
            </div>
            <div className="prompt-footer"><span><CircleDot size={12} /> {t("agentInspect")}</span><span className="prompt-footer-right"><LockKeyhole size={12} /> {t("localExecutionDetail")}</span></div>{taskError ? <div className="repo-error task-error">{taskError}</div> : null}
          </section>

          <div className="content-grid">
            <section className="panel task-panel">
              <div className="panel-header"><div><div className="section-kicker">{t("inFlight")}</div><h2>{t("taskQueue")}</h2></div><button className="text-button" onClick={() => setActiveNav("tasks")}>{t("viewAll")} <ArrowUpRight size={14} /></button></div>
              <div className="task-filter"><label htmlFor="task-filter">{t("task.filter")}</label><select id="task-filter" value={taskFilter} onChange={event => setTaskFilter(event.target.value)}>{["all", "queued", "running", "review", "done", "failed", "paused", "cancelled"].map(status => <option value={status} key={status}>{status === "all" ? t("task.all") : t(status)}</option>)}</select></div>
              <div className="task-list">
                {(activeNav === "tasks" ? filteredTasks : filteredTasks.slice(0, 4)).map((task) => <TaskRow key={task.id} task={task} active={task.id === activeTaskId} onClick={() => { if (!patchRunning && !gitRunning && !iterationRunning && !testRunning) { setActiveTaskId(task.id); setActiveNav("runs"); } }} />)}
                {!filteredTasks.length ? <div className="empty-operation queue-empty"><Zap size={17} /><span>{tasks.length ? t("task.noMatch") : t("noTasks")}</span></div> : null}
              </div>
              <button className="add-task-button" onClick={() => document.querySelector<HTMLInputElement>(".prompt-row input")?.focus()}><Plus size={15} /> {t("startAnotherTask")}</button>
            </section>

            <section className="panel stream-panel">
              <div className="panel-header"><div><div className="section-kicker">TRACE / {activeTask?.id.toUpperCase()}</div><h2>{t("executionStream")}</h2></div><span className="live-badge"><span className="pulse-dot" /> {t("liveEvents")}</span></div>
              {activeTask?.requiresRecovery ? <div className="recovery-note" role="status"><AlertTriangle size={14} /><span>{t("recovery.title")}</span><button className="small-action" onClick={() => void confirmRecovery()} disabled={iterationRunning}>{t("recovery.action")}</button></div> : null}
              {activeTask ? <div className="iteration-bar"><div className="iteration-status"><strong>{t("iteration")} {activeTask.currentIteration || iterations.length || 0}</strong><span>{activeTask.nextAction ?? activeTask.plan?.objective ?? t("currentCodingLoop")}</span></div><div className="iteration-actions">{activeTask.status === "failed" ? <button className="small-action" onClick={() => void beginRepair()} disabled={iterationRunning}>{t("repair")}</button> : activeTask.status === "paused" ? <button className="small-action" onClick={() => void changeTaskState("resume")} disabled={iterationRunning}>{t("resume")}</button> : (activeTask.status === "review" && !iterations.length || activeTask.lifecycleStatus === "coding" && Boolean(iterations.at(-1)?.rolled_back)) ? planApproval?.status === "approved" ? <button className="small-action" onClick={() => void beginIteration()} disabled={iterationRunning || Boolean(activeTask.repositoryId && !activeTask.taskBranch)} title={activeTask.repositoryId && !activeTask.taskBranch ? t("createBranchBeforeCommit") : undefined}>{t("startIteration")}</button> : <button className="small-action" onClick={() => void requestPlanApproval()} disabled={iterationRunning}>{planApproval ? t("awaitingApproval") : t("requestPlanApproval")}</button> : null}{planApproval?.status === "pending" ? <button className="small-action small-action--muted" onClick={() => void approvePlan()} disabled={iterationRunning}>{t("approvePlan")}</button> : null}{["coding", "testing", "running"].includes(activeTask.status) ? <button className="small-action small-action--muted" onClick={() => void changeTaskState("pause")} disabled={iterationRunning}>{t("pause")}</button> : null}{!["cancelled", "done"].includes(activeTask.status) ? <button className="text-button" onClick={() => void changeTaskState("cancel")} disabled={iterationRunning}>{t("cancel")}</button> : null}</div></div> : null}
              {iterationError ? <div className="inline-error">{iterationError}</div> : null}
              {activeTask?.plan ? <div className="plan-list"><div className="plan-label">{t("plan")} / {activeTask.plan.steps.length} {t("steps")}</div>{activeTask.plan.steps.map((step, index) => <div className="plan-step" key={step.id}><span className={`plan-step-index ${index === 0 ? "plan-step-index--active" : ""}`}>{index + 1}</span><div className="plan-step-copy"><strong>{step.title}</strong><span>{step.description}</span></div><span className={`plan-risk plan-risk--${step.risk}`}>{t(`risk.${step.risk}`)}</span></div>)}</div> : null}
              {iterations.length ? <div className="iteration-history"><div className="plan-label">{t("iterations")} / {iterations.length}</div>{iterations.map((iteration) => <div className="iteration-row" key={iteration.id}><span className={`iteration-dot iteration-dot--${iteration.status}`} /><div className="iteration-row-copy"><strong>#{iteration.number} {t(`iterationStatus.${iteration.status}`)}</strong><span>{iteration.goal}</span>{iteration.failure_summary ? <small>{iteration.failure_summary}</small> : null}</div>{iteration.test_result?.failed_tests?.[0] ? <span className="failure-location">{iteration.test_result.failed_tests[0].path ?? t("testOutput")}{iteration.test_result.failed_tests[0].line ? `:${iteration.test_result.failed_tests[0].line}` : ""}</span> : null}</div>)}</div> : null}
              <div className="stream-list">
                {displayedStreamEvents.map((event) => {
                  const Icon = iconForEvent(event.icon);
                  return <div className={`stream-event stream-event--${event.status}`} key={event.title}><div className="stream-time">{event.time}</div><div className="stream-marker"><Icon size={14} /></div><div className="stream-copy"><strong>{event.title}</strong><span>{event.detail}</span></div>{event.status === "done" ? <Check size={15} className="stream-check" /> : event.status === "active" ? <span className="stream-spinner" /> : <Clock3 size={15} className="stream-wait" />}</div>;
                })}
                {!displayedStreamEvents.length ? <div className="empty-operation"><Activity size={17} /><span>{t("selectTaskEvents")}</span></div> : null}
              </div>
              {eventsError ? <div className="inline-error">{eventsError}</div> : null}
              <div className="stream-actions">
                <button className="console-link" onClick={() => setActiveNav("runs")}><TerminalSquare size={14} /> {t("openRunConsole")} <ArrowUpRight size={13} /></button>
                <span className="event-source">{runEvents.length ? `${runEvents.length} ${t("recordedEvents")}` : t("waitingEvents")}</span>
              </div>
            </section>
          </div>

          <section className="operations-grid">
            <section className="panel test-panel">
              <div className="panel-header"><div><div className="section-kicker"><PackageCheck size={13} /> {t("validation")}</div><h2>{t("testConsole")}</h2></div><button className="small-action" onClick={() => void executeTests()} disabled={!activeTask?.runId || testRunning || !iterations.length || Boolean(iterations.at(-1)?.rolled_back) || ["paused", "cancelled", "done"].includes(activeTask.status)}>{testRunning ? <><Activity size={13} className="spin" /> {t("running")}</> : <><TerminalSquare size={13} /> {t("runTests")}</>}</button></div>
              <div className="validation-options"><label>{t("validation.kind")}<select value={testKind} onChange={event => setTestKind(event.target.value as TestKind)} disabled={testRunning}>{["auto", "pytest", "frontend", "static"].map(kind => <option value={kind} key={kind}>{t("validation." + kind)}</option>)}</select></label><input value={testTarget} onChange={event => setTestTarget(event.target.value)} aria-label={t("validation.target")} placeholder={t("validation.target")} disabled={testRunning} /></div>
              {testResult ? <div className="test-result">
                <div className="test-result-top"><span className={`result-badge result-badge--${testResult.status}`}>{t("validation." + testResult.status)}</span><span>{testResult.duration_ms} ms · exit {testResult.exit_code ?? "—"}</span></div>
                <code className="test-command">{testResult.command.length ? testResult.command.join(" ") : t("noCommandDetected")}</code>
                {testResult.stdout || testResult.stderr ? <pre className="test-output">{[testResult.stdout, testResult.stderr].filter(Boolean).join("\n")}</pre> : <span className="empty-detail">{t("noOutput")}</span>}
                {testResult.failed_tests.map((failure, index) => failure.path ? <button className="path-link failure-link" key={index} onClick={() => openFailure(failure.path!, failure.line ?? 1)}>{failure.path}:{failure.line ?? 1}</button> : null)}
                {testResult.output_truncated ? <span className="output-note">{t("outputTruncated")}</span> : null}
              </div> : <div className="empty-operation"><TerminalSquare size={17} /><span>{t("runDetectedTests")}</span></div>}
              {testError ? <div className="inline-error">{testError}</div> : null}
            </section>

            <section className="panel patch-panel">
              <div className="panel-header"><div><div className="section-kicker"><FileCode2 size={13} /> {t("changeReview")}</div><h2>{t("patchPreview")}</h2></div><span className="patch-safety"><LockKeyhole size={12} /> {t("checkpointed")}</span></div>
              <div className="patch-form">
                <label className="patch-operation">{t("patch.operation")}<select value={patchOperation} onChange={event => { setPatchOperation(event.target.value as PatchFileInput["operation"]); setPatchPreviewResult(null); }}>{["update", "create", "delete"].map(operation => <option value={operation} key={operation}>{t("patch." + operation)}</option>)}</select></label>
                <input value={patchPath} onChange={(event) => { setPatchPath(event.target.value); setPatchExpectedHash(undefined); setPatchPreviewResult(null); }} placeholder={t("patchPathPlaceholder")} aria-label={t("patchPath")} />
                <textarea value={patchContent} disabled={patchOperation === "delete"} onChange={(event) => { setPatchContent(event.target.value); setPatchPreviewResult(null); }} placeholder={t("patchContentPlaceholder")} aria-label={t("patchContent")} rows={4} />
                <div className="patch-form-actions"><span>{t("updateOnly")}</span><button className="small-action" onClick={() => void previewActivePatch()} disabled={!activeTask?.runId || !patchPath.trim() || patchRunning}>{patchRunning ? t("working") : t("previewDiff")}</button></div>
              </div>
              {patchPreviewResult ? <div className="patch-result"><div className="patch-summary"><span>{patchPreviewResult.files.length} {t("file")}</span><span className="diff-add">+{patchPreviewResult.additions}</span><span className="diff-del">-{patchPreviewResult.deletions}</span><span>{patchPreviewResult.bytes_changed} {t("bytes")}</span></div><pre className="diff-view">{patchPreviewResult.diff || t("noChangesDetected")}</pre><div className="patch-result-actions"><span>{t("reviewUnifiedDiff")}</span><div className="patch-result-buttons"><button className="small-action small-action--muted" onClick={cancelPatchPreview} disabled={patchRunning}>{t("cancel")}</button><button className="small-action" onClick={() => void applyActivePatch()} disabled={patchRunning || !patchPreviewResult.diff}>{t("applyCheckpoint")}</button></div></div></div> : <div className="empty-operation"><FileCode2 size={17} /><span>{t("previewBeforeRepository")}</span></div>}
              {patchApplied ? <div className="success-note"><Check size={14} /> {patchApplied}</div> : null}
              {patchError ? <div className="inline-error">{patchError}</div> : null}
            </section>
          </section>

          <section className="operations-grid git-grid">
            <section className="panel git-panel">
              <div className="panel-header"><div><div className="section-kicker"><GitBranch size={13} /> {t("branchIsolation")}</div><h2>{t("taskBranch")}</h2></div>{gitSnapshot ? <span className="patch-safety">{gitSnapshot.clean ? t("clean") : `${gitSnapshot.changed_files.length} ${t("changed")}`}</span> : null}</div>
              {gitSnapshot ? <div className="git-snapshot"><div><span>{t("branch")}</span><strong>{gitSnapshot.branch}</strong></div><div><span>HEAD</span><code>{gitSnapshot.head.slice(0, 10)}</code></div></div> : <div className="empty-operation"><GitBranch size={17} /><span>{t("createBranchBeforeCommit")}</span></div>}
              <div className="git-form"><input value={branchName} onChange={(event) => setBranchName(event.target.value)} placeholder="feat/task-short-name" aria-label={t("branchName")} /><button className="small-action" onClick={() => void createBranch()} disabled={!activeTask || !branchName.trim() || gitRunning}><GitBranch size={13} /> {t("createBranch")}</button></div>
            </section>
            <section className="panel git-panel">
              <div className="panel-header"><div><div className="section-kicker"><GitPullRequest size={13} /> {t("commitReview")}</div><h2>{t("commitPreview")}</h2></div><span className="patch-safety"><LockKeyhole size={12} /> {t("localOnly")}</span></div>
              <div className="git-form"><input value={commitMessage} onChange={(event) => { setCommitMessage(event.target.value); setCommitPreviewResult(null); }} placeholder={t("commitMessagePlaceholder")} aria-label={t("commitMessage")} /><button className="small-action" onClick={() => void previewTaskCommit()} disabled={!activeTask || !commitMessage.trim() || gitRunning}>{t("previewCommit")}</button></div>
              {commitPreviewResult ? <div className="patch-result"><div className="patch-summary"><span>{commitPreviewResult.changed_files.length} {t("files")}</span><span>{commitPreviewResult.branch}</span><span>{commitPreviewResult.ready ? t("ready") : t("empty")}</span></div><pre className="diff-view">{commitPreviewResult.diff || t("noUncommittedChanges")}</pre>{commitPreviewResult.excluded_files.length ? <p className="commit-excluded">{t("commit.excluded")}: {commitPreviewResult.excluded_files.join(", ")}</p> : null}<div className="patch-result-actions"><span>{t("testsBeforeCommit")}</span><div className="patch-result-buttons">{writeApproval?.status === "approved" ? <button className="small-action" onClick={() => void commitTask()} disabled={!commitPreviewResult.ready || gitRunning}>{t("createCommit")}</button> : <><button className="small-action small-action--muted" onClick={() => void requestWriteApproval()} disabled={!commitPreviewResult.ready || gitRunning}>{writeApproval ? t("awaitingApproval") : t("requestWriteApproval")}</button>{writeApproval?.status === "pending" ? <button className="small-action" onClick={() => void approveWrite()} disabled={gitRunning}>{t("approve")}</button> : null}</>}</div></div></div> : <div className="empty-operation"><GitPullRequest size={17} /><span>{t("previewLocalDiff")}</span></div>}
              {gitError ? <div className="inline-error">{gitError}</div> : null}
            </section>
          </section>

          {activeTask?.runId && activeNav !== "repositories" ? <RunHistory runId={activeTask.runId} revision={iterations.map(item => item.updated_at + String(item.rolled_back)).join()} onRestored={refreshActiveTask} disabled={testRunning || patchRunning || gitRunning || ["cancelled", "done"].includes(activeTask.status)} /> : null}

          <section className="lower-grid">
            <section className="panel repo-panel"><div className="panel-header"><div><div className="section-kicker">{t("codebasePulse")}</div><h2>{t("repositories")}</h2></div><button className="small-action" onClick={() => void addRepository()}><Plus size={13} /> {t("add")}</button></div>{repositories.length ? repositories.map((repository, index) => <RepoRow key={repository.id} repository={repository} tone={["amber", "cyan", "lime"][index % 3]} t={t} />) : <div className="repo-empty"><GitBranch size={18} /><span>{t("noRepositoryConnected")}</span><button className="text-button" onClick={() => void addRepository()}>{t("addRepository")} <ArrowUpRight size={13} /></button></div>}{repositoryError ? <div className="repo-error">{repositoryError}</div> : null}</section>
            <section className="panel approval-panel"><div className="panel-header"><div><div className="section-kicker">{t("humanInTheLoop")}</div><h2>{t("needsYourCall")}</h2></div><span className="approval-count">{String(reviewTasks.length).padStart(2, "0")}</span></div>{reviewTasks.length ? reviewTasks.slice(0, 3).map((task) => <div className="approval-item" key={task.id}><div className="approval-icon approval-icon--rose"><ShieldCheck size={16} /></div><div className="approval-copy"><strong>{t("reviewTaskPlan")}</strong><span>{task.id} · {task.title}</span></div><button className="small-action" onClick={() => setActiveTaskId(task.id)}>{t("review")}</button></div>) : <div className="empty-operation"><ShieldCheck size={17} /><span>{t("noApprovalTasks")}</span></div>}</section>
          </section>

          <footer className="page-footer"><span><span className="footer-mark" /> {t("forgeRunningLocally")}</span><span>{t("lastSynced")}</span></footer>
        </div>
      </main>
    </div>
  );
}

function MetricCard({ label, value, delta, icon: Icon, tone }: { label: string; value: string; delta: string; icon: typeof Activity; tone: string }) {
  return <div className={`metric-card metric-card--${tone}`}><div className="metric-top"><span>{label}</span><span className="metric-icon"><Icon size={15} /></span></div><div className="metric-value">{value}</div><div className="metric-delta"><span className="delta-line" />{delta}</div></div>;
}

function TaskRow({ task, active, onClick }: { task: Task; active: boolean; onClick: () => void }) {
  const { t } = usePreferences();
  const statusLabel = task.status === "running" ? t("running") : task.status === "review" ? t("review") : task.status === "failed" ? t("failed") : task.status === "done" ? t("done") : task.status === "paused" ? t("paused") : task.status === "cancelled" ? t("cancelled") : t("queued");
  return <button className={`task-row ${active ? "task-row--active" : ""}`} onClick={onClick}><span className={`task-accent task-accent--${task.accent}`} /><span className="task-main"><span className="task-title">{task.title}</span><span className="task-meta"><span>{task.repo}</span><span className="meta-separator">·</span><span>{task.branch}</span></span></span><span className={`task-status task-status--${task.status}`}>{task.status === "running" ? <><span className="pulse-dot" />{statusLabel}</> : statusLabel}</span><span className="task-time">{task.time === "now" ? t("now") : task.time}</span></button>;
}

function RepoRow({ repository, tone, t }: { repository: Repository; tone: string; t: (key: string) => string }) {
  const language = Object.keys(repository.languages)[0] ?? t("unknown");
  const status = repository.changed_files ? `${repository.changed_files} ${t("changes")}` : t("synced");
  return <div className="repo-row"><div className={`repo-icon repo-icon--${tone}`}><Box size={15} /></div><div className="repo-copy"><strong>{repository.name}</strong><span>{language} · {repository.branch}</span></div><span className={`repo-status repo-status--${tone}`}><span className="mini-dot" />{status}</span></div>;
}

export default App;
