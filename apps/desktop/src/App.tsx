import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Box,
  Check,
  CircleDot,
  Clock3,
  Code2,
  Command,
  FileCode2,
  FolderOpen,
  FolderSync,
  GitBranch,
  GitPullRequest,
  LayoutDashboard,
  Languages,
  ListChecks,
  LockKeyhole,
  Menu,
  Monitor,
  MoreHorizontal,
  PackageCheck,
  Plus,
  Rocket,
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
  sendCoworkChat,
  getApiHealth,
  getTask,
  getTaskGitSnapshot,
  getWorkspaceSummary,
  listIterations,
  listApprovals,
  listRepositories,
  listRunEvents,
  listTasks,
  draftPatch,
  startAgentLoop,
  stopAgentLoop,
  getAgentState,
  previewPatch,
  previewCommit,
  registerRepository,
  resumeTask,
  runTests,
  requestApproval,
  createCommit,
  createTaskBranch,
  createPullRequest,
  decideApproval,
  getPublishState,
  getRemoteTarget,
  previewPullRequest,
  pushTaskBranch,
  refreshPullRequest,
  startIteration,
  startRepair,
  pauseTask,
  cancelTask,
  recoverTask,
  type AgentTask,
  type AgentMode,
  type TaskTrack,
  type TaskBudget,
  type AgentAdvanceAction,
  type AgentAction,
  type AuditEvent,
  type CommitPreview,
  type GitSnapshot,
  type Approval,
  type PatchPreview,
  type PublishState,
  type PullRequestPreview,
  type RemoteTarget,
  type Repository,
  type TestRunResult,
  type TaskIteration,
  type TestKind,
  type SourceFile,
  type PatchFileInput,
  type WorkspaceSummary,
} from "./lib/api";
import { usePreferences, type Language, type ThemeMode } from "./lib/preferences";
import { desktopBridge, desktopStatus, isDesktopShell, type DesktopStatus } from "./lib/desktop";

import { Dialog, AsyncMessage } from "@coding-agent/ui";
import { RepositoryBrowser } from "./components/RepositoryBrowser";
import { RunHistory } from "./components/RunHistory";
import { AgentPanel } from "./components/AgentPanel";
import { ApprovalPanel } from "./components/ApprovalPanel";
import { ChatPanel } from "./components/ChatPanel";
import { ValidationChecklist } from "./components/ValidationChecklist";

type NavKey = "chat" | "overview" | "tasks" | "repositories" | "runs" | "approvals";

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
  updatedAt: string;
  title: string;
  repo: string;
  branch: string;
  status: "running" | "queued" | "review" | "done" | "failed" | "paused" | "cancelled";
  time: string;
  accent: string;
  plan?: AgentTask["plan"];
  mode: AgentMode;
  track: TaskTrack;
  validationChecks: AgentTask["validation_checks"];
};

type StreamEvent = {
  id: string;
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
    updatedAt: task.updated_at,
    title: task.goal,
    repo: task.plan?.repository_summary ?? "workspace",
    branch: task.branch ?? task.run_id,
    status: task.status === "planning" || task.status === "queued" ? "queued" : task.status === "failed" ? "failed" : task.status === "cancelled" ? "cancelled" : task.status === "paused" ? "paused" : task.status === "done" || task.status === "completed" || task.status === "pr_created" ? "done" : task.status === "review" || task.status === "awaiting_approval" || task.status === "ready_for_pr" ? "review" : "running",
    time: "now",
    accent: task.status === "failed" ? "rose" : "amber",
    plan: task.plan,
    mode: task.mode,
    track: task.track,
    validationChecks: task.validation_checks ?? [],
  };
}

const navItems: Array<{ key: NavKey; icon: typeof LayoutDashboard }> = [
  { key: "overview", icon: LayoutDashboard },
  { key: "repositories", icon: FolderOpen },
  { key: "runs", icon: TerminalSquare },
  { key: "approvals", icon: ShieldCheck },
];

function eventToStreamEvent(event: AuditEvent, t: (key: string) => string, locale: Language): StreamEvent {
  const action = event.action.toLowerCase();
  const icon: StreamEvent["icon"] = action.includes("test") ? "test" : action.includes("patch") ? "code" : action.includes("repository") || action.includes("search") ? "search" : "approve";
  const title = action === "task.create" ? t("taskCreatedEvent") : action === "patch.preview" ? t("patchPreviewEvent") : action === "patch.apply" ? t("patchAppliedEvent") : action === "tests.run" ? t("testsCompletedEvent") : action === "checkpoint.rollback" ? t("checkpointRestoredEvent") : event.action;
  const details = event.details;
  const command = Array.isArray(details.command) ? details.command.join(" ") : undefined;
  const detail = command ? `${event.summary} · ${command}` : event.summary;
  return {
    id: event.id,
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
  const [activeNav, setActiveNav] = useState<NavKey>("chat");
  const [tasks, setTasks] = useState(initialTasks);
  const [activeTaskId, setActiveTaskId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [loopRunning, setLoopRunning] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [chatMode, setChatMode] = useState<AgentMode>("standard");
  const [chatPending, setChatPending] = useState<Approval | null>(null);
  const [chatBudget, setChatBudget] = useState<TaskBudget | null>(null);
  const [chatComposingNew, setChatComposingNew] = useState(false);
  const [chatSending, setChatSending] = useState(false);
  const chatSendingRef = useRef(false);
  const [chatAction, setChatAction] = useState<AgentAction | null>(null);
  const [railTab, setRailTab] = useState<"cowork" | "code">("cowork");
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
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
  const [validationSaving, setValidationSaving] = useState(false);
  const [patchPath, setPatchPath] = useState("");
  const [patchContent, setPatchContent] = useState("");
  const [patchPreviewResult, setPatchPreviewResult] = useState<PatchPreview | null>(null);
  const [patchRunning, setPatchRunning] = useState(false);
  const [patchApplied, setPatchApplied] = useState<string | null>(null);
  const [patchRationale, setPatchRationale] = useState<string | null>(null);
  const [patchError, setPatchError] = useState<string | null>(null);
  const [iterations, setIterations] = useState<TaskIteration[]>([]);
  const [iterationRunning, setIterationRunning] = useState(false);
  const [iterationError, setIterationError] = useState<string | null>(null);
  const [agentBusyAction, setAgentBusyAction] = useState<AgentAdvanceAction | null>(null);
  const [gitSnapshot, setGitSnapshot] = useState<GitSnapshot | null>(null);
  const [branchName, setBranchName] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const [commitPreviewResult, setCommitPreviewResult] = useState<CommitPreview | null>(null);
  const [gitRunning, setGitRunning] = useState(false);
  const [gitError, setGitError] = useState<string | null>(null);
  const [publishState, setPublishState] = useState<PublishState | null>(null);
  const [remoteTarget, setRemoteTarget] = useState<RemoteTarget | null>(null);
  const [pullRequestPreview, setPullRequestPreview] = useState<PullRequestPreview | null>(null);
  const [pullRequestTarget, setPullRequestTarget] = useState("main");
  const [publishRunning, setPublishRunning] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  const pushKeyRef = useRef("");
  const pullRequestKeyRef = useRef("");
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [approvalCount, setApprovalCount] = useState(0);
  const [approvalRunning, setApprovalRunning] = useState(false);
  const [selectedRepositoryId, setSelectedRepositoryId] = useState("");
  const [sourceLocation, setSourceLocation] = useState<{ path: string; line: number } | null>(null);
  const [taskFilter, setTaskFilter] = useState("all");
  const [testKind, setTestKind] = useState<TestKind>("auto");
  const [testTarget, setTestTarget] = useState("");
  const [patchExpectedHash, setPatchExpectedHash] = useState<string>();
  const [patchOperation, setPatchOperation] = useState<PatchFileInput["operation"]>("update");
  const [repositoryDialogOpen, setRepositoryDialogOpen] = useState(false);
  const [repositoryPathInput, setRepositoryPathInput] = useState("");
  const [desktopRuntime, setDesktopRuntime] = useState<DesktopStatus | null>(desktopStatus);
  const [repositorySaving, setRepositorySaving] = useState(false);
  const repositoryOpeningRef = useRef(false);
  const [confirmAction, setConfirmAction] = useState<"cancel" | "delete" | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const promptInputRef = useRef<HTMLInputElement>(null);
  const taskRef = useRef(activeTaskId);
  useEffect(() => { taskRef.current = activeTaskId; }, [activeTaskId]);
  useEffect(() => { if (selectedRepositoryId && !repositories.some(repository => repository.id === selectedRepositoryId)) setSelectedRepositoryId(""); }, [repositories, selectedRepositoryId]);

  const activeTask = useMemo(
    () => tasks.find((task) => task.id === activeTaskId),
    [activeTaskId, tasks],
  );
  const activeRepository = repositories.find(repository => repository.id === activeTask?.repositoryId);
  const activeTaskHasGit = activeRepository?.has_git === true;

  // Two stores: Cowork (chat, flat by recency) and Code (coding, grouped by folder).
  const coworkTasks = useMemo(() => tasks.filter((task) => task.track === "cowork"), [tasks]);
  const railGroups = useMemo(() => {
    const groups = new Map(repositories.map(repository => [repository.id, { id: repository.id, name: repository.name, path: repository.path, rows: [] as Task[] }]));
    for (const task of tasks) {
      if (task.track !== "code") continue;
      const key = task.repositoryId ?? task.repo;
      const group = groups.get(key) ?? { id: key, name: task.repo, path: "", rows: [] as Task[] };
      group.rows.push(task);
      groups.set(key, group);
    }
    return [...groups.values()];
  }, [repositories, tasks]);

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
    if (!activeTaskId || !activeTaskHasGit) {
      setGitSnapshot(null);
      return;
    }
    const controller = new AbortController();
    getTaskGitSnapshot(activeTaskId, controller.signal).then(setGitSnapshot).catch(() => setGitSnapshot(null));
    return () => controller.abort();
  }, [activeTaskId, activeTaskHasGit]);

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
    if (!desktopBridge) return;
    return desktopBridge.onStatusChanged(setDesktopRuntime);
  }, []);

  useEffect(() => {
    pushKeyRef.current = "";
    pullRequestKeyRef.current = "";
    setPullRequestPreview(null);
    setPublishError(null);
    if (!activeTaskId || !activeTaskHasGit) {
      setPublishState(null);
      setRemoteTarget(null);
      return;
    }
    const controller = new AbortController();
    getPublishState(activeTaskId, controller.signal).then(setPublishState).catch(() => setPublishState(null));
    getRemoteTarget(activeTaskId, controller.signal)
      .then((payload) => { setRemoteTarget(payload); setPullRequestTarget(payload.default_branch); })
      .catch(() => setRemoteTarget(null));
    return () => controller.abort();
  }, [activeTaskId, activeTaskHasGit]);

  useEffect(() => {
    if (!activeTaskId) {
      setIterations([]);
      return;
    }
    const controller = new AbortController();
    listIterations(activeTaskId, controller.signal).then((payload) => { setIterations(payload.items); setTestResult(payload.items.at(-1)?.test_result ?? null); }).catch(() => setIterations([]));
    return () => controller.abort();
  }, [activeTaskId]);

  // Chat view poll: the autonomous loop runs server-side, so refresh the agent state, task
  // and event stream on an interval while a CODE chat is open (Cowork chat is synchronous, see below).
  useEffect(() => {
    if (activeNav !== "chat" || chatComposingNew || activeTask?.track === "cowork" || !activeTask?.runId) {
      setLoopRunning(false);
      return;
    }
    const runId = activeTask.runId;
    const taskId = activeTask.id;
    let disposed = false;
    const tick = async () => {
      try {
        const [state, events] = await Promise.all([getAgentState(taskId), listRunEvents(runId)]);
        if (disposed) return;
        setLoopRunning(state.loop_running);
        setRunEvents(events.items);
        setChatPending(state.pending_approval ?? null);
        setChatBudget(state.task.budget ?? null);
        setChatAction(state.action);
        updateTaskFromApi(state.task);
      } catch {
        /* transient poll failure — the next tick retries */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 2500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [activeNav, activeTask?.runId, activeTask?.id, activeTask?.track, chatComposingNew]);

  // Cowork is a plain chat: no loop. Just load its transcript (chat.* events) when it is opened.
  useEffect(() => {
    if (activeNav !== "chat" || chatComposingNew || activeTask?.track !== "cowork" || !activeTask?.runId) return;
    let disposed = false;
    listRunEvents(activeTask.runId).then((payload) => { if (!disposed) setRunEvents(payload.items); }).catch(() => { /* retry on next open */ });
    return () => { disposed = true; };
  }, [activeNav, chatComposingNew, activeTask?.track, activeTask?.runId, activeTask?.id]);

  const loadOverviewData = async () => {
    setOverviewLoading(true);
    setOverviewError(null);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const [summary, taskList, repoList] = await Promise.all([
        getWorkspaceSummary(controller.signal),
        listTasks(controller.signal),
        listRepositories(controller.signal),
      ]);
      setWorkspaceSummary(summary);
      if (taskList.items.length) {
        const nextTasks = taskList.items.map(taskFromApi);
        setTasks((current) => [...nextTasks, ...current.filter((task) => !nextTasks.some((item) => item.id === task.id))]);
        if (!taskRef.current) {
          setActiveTaskId(nextTasks[0].id);
          setSelectedRepositoryId(nextTasks[0].repositoryId ?? "");
          setRailTab(nextTasks[0].track);
        }
      }
      setRepositories(repoList.items);
    } catch (error) {
      if (controller.signal.aborted) {
        setOverviewError(t("overview.timeout"));
      } else {
        setOverviewError(error instanceof Error ? error.message : t("overview.error"));
      }
    } finally {
      clearTimeout(timeout);
      setOverviewLoading(false);
    }
  };

  useEffect(() => {
    void loadOverviewData();
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        promptInputRef.current?.focus();
        promptInputRef.current?.select();
      } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        const target = event.target as HTMLElement | null;
        if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
        event.preventDefault();
        setActiveNav("repositories");
      } else if (event.key === "Escape") {
        if (repositoryDialogOpen) {
          setRepositoryDialogOpen(false);
        } else if (confirmAction) {
          setConfirmAction(null);
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [repositoryDialogOpen, confirmAction]);

  const pageTitle = t(`nav.${activeNav}`);
  const displayedStreamEvents = runEvents.map((event) => eventToStreamEvent(event, t, language));
  const reviewTasks = tasks.filter((task) => task.status === "review");
  const planApproval = approvals.find((item) => item.type === "plan");
  const writeApproval = approvals.find((item) => item.type === "write" && item.scope_hash === commitPreviewResult?.scope_hash);
  const pushApproval = approvals.find((item) => item.type === "push");
  const pullRequestApproval = approvals.find((item) => item.type === "pr");

  const runTask = async () => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt || isRunning || agentBusyAction) return;
    setTaskError(null);
    setIsRunning(true);
    setPrompt("");
    try {
      if (!selectedRepositoryId) throw new Error(t("chat.needRepository"));
      const task = await createTask(trimmedPrompt, selectedRepositoryId);
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

  const sendChatMessage = async (message: string): Promise<boolean> => {
    const trimmed = message.trim();
    if (!trimmed || loopRunning || chatSendingRef.current) return false;
    chatSendingRef.current = true;
    setChatSending(true);
    setChatError(null);
    const track: TaskTrack = chatComposingNew ? railTab : (activeTask?.track ?? railTab);

    try {
      if (track === "cowork") {
        // Cowork: a plain synchronous chat. Create the conversation on the first message, then post turns.
        let taskId = chatComposingNew ? "" : (activeTask?.id ?? "");
        let runId = chatComposingNew ? "" : (activeTask?.runId ?? "");
        if (!taskId) {
          const created = await createTask(trimmed, selectedRepositoryId || undefined, undefined, "cowork");
          const newTask = taskFromApi(created);
          setTasks((current) => [newTask, ...current.filter((item) => item.id !== newTask.id)]);
          setActiveTaskId(newTask.id);
          setChatComposingNew(false);
          taskId = newTask.id;
          runId = created.run_id;
        }
        await sendCoworkChat(taskId, trimmed);
        if (runId) setRunEvents((await listRunEvents(runId)).items);
        return true;
      }

      // Code works in ordinary folders too; only Git publishing requires a repository.
      const runId = chatComposingNew ? undefined : activeTask?.runId;
      const completedLocalTask = !chatComposingNew && activeTask?.lifecycleStatus === "ready_for_pr" && activeRepository?.has_git === false;
      if (!runId || completedLocalTask || ["done", "cancelled"].includes(activeTask?.status ?? "")) {
        const repositoryId = completedLocalTask ? activeTask?.repositoryId : selectedRepositoryId;
        const mode = completedLocalTask && activeTask ? activeTask.mode : chatMode;
        if (!repositoryId) throw new Error(t("chat.needRepository"));
        const created = await createTask(trimmed, repositoryId, mode, "code");
        const newTask = taskFromApi(created);
        setTasks((current) => [newTask, ...current.filter((item) => item.id !== newTask.id)]);
        setActiveTaskId(newTask.id);
        setChatComposingNew(false);
        setChatPending(null);
        await startAgentLoop(created.run_id, { mode });
      } else {
        await startAgentLoop(runId, { feedback: trimmed });
      }
      setLoopRunning(true);
      setChatAction(null);
      return true;
    } catch (error) {
      setChatError(error instanceof Error ? error.message : t("chat.error"));
      return false;
    } finally {
      chatSendingRef.current = false;
      setChatSending(false);
    }
  };

  const stopChatLoop = async () => {
    if (!activeTask?.runId) return;
    try {
      const state = await stopAgentLoop(activeTask.runId);
      setLoopRunning(state.loop_running);
    } catch (error) {
      setChatError(error instanceof Error ? error.message : t("chat.error"));
    }
  };

  // Inline approval: decide the pending push/pr/plan/write right in chat, then resume the loop on approve.
  const decideChatApproval = async (approvalId: string, decision: "approve" | "reject", reason?: string) => {
    if (chatSendingRef.current) return;
    chatSendingRef.current = true;
    setChatSending(true);
    setChatError(null);
    try {
      await decideApproval(approvalId, decision, reason);
      setChatPending(null);
      if (decision === "approve" && activeTask?.runId) {
        await startAgentLoop(activeTask.runId, {});  // resume with the task's existing posture
        setLoopRunning(true);
      }
    } catch (error) {
      setChatError(error instanceof Error ? error.message : t("chat.error"));
    } finally {
      chatSendingRef.current = false;
      setChatSending(false);
    }
  };

  const startNewChatSession = () => {
    setActiveNav("chat");
    setChatComposingNew(true);
    setActiveTaskId("");
    setRunEvents([]);
    setChatPending(null);
    setChatBudget(null);
    setChatAction(null);
    setChatError(null);
    setLoopRunning(false);
  };

  const selectChatSession = (taskId: string) => {
    setActiveNav("chat");
    setChatComposingNew(false);
    setChatPending(null);
    setChatAction(null);
    setChatError(null);
    setActiveTaskId(taskId);
    const selectedTask = tasks.find(task => task.id === taskId);
    setSelectedRepositoryId(selectedTask?.repositoryId ?? "");
    if (selectedTask) setRailTab(selectedTask.track);
  };

  const openRegisteredFolder = (repositoryId: string) => {
    setSelectedRepositoryId(repositoryId);
    setSourceLocation(null);
    setRailTab("code");
    startNewChatSession();
    setSidebarOpen(false);
    setRepositoryDialogOpen(false);
    setRepositoryError(null);
  };

  const openLocalFolder = async (path?: string) => {
    if (repositoryOpeningRef.current) return;
    if (!path && !desktopBridge) {
      setRepositoryError(null);
      setRepositoryDialogOpen(true);
      return;
    }
    repositoryOpeningRef.current = true;
    setRepositorySaving(true);
    setRepositoryError(null);
    try {
      const picked = path || await desktopBridge!.pickDirectory({ fallbackPath: desktopRuntime?.workspaceRoot });
      if (!picked) return;
      setRepositoryPathInput(picked);
      const repository = await registerRepository(picked);
      setRepositories(current => [repository, ...current.filter(item => item.id !== repository.id)]);
      openRegisteredFolder(repository.id);
      setRepositoryPathInput("");
      void getWorkspaceSummary().then(setWorkspaceSummary).catch(() => {});
    } catch (error) {
      setRepositoryError(error instanceof Error ? error.message : t("repositoryError"));
      setRepositoryDialogOpen(true);
    } finally {
      repositoryOpeningRef.current = false;
      setRepositorySaving(false);
    }
  };

  const addRepository = () => { void openLocalFolder(); };
  const saveRepository = () => { if (repositoryPathInput.trim()) void openLocalFolder(repositoryPathInput.trim()); };

  const changeWorkspaceRoot = async () => {
    if (!desktopBridge) return;
    setRepositorySaving(true);
    setRepositoryError(null);
    try {
      const result = await desktopBridge.pickWorkspaceRoot();
      if (result.canceled) return;
      setDesktopRuntime((current) => ({ ...(current ?? {}), ...result } as DesktopStatus));
      if (result.error) setRepositoryError(result.error);
    } catch (error) {
      setRepositoryError(error instanceof Error ? error.message : t("repositoryError"));
    } finally {
      setRepositorySaving(false);
    }
  };

  const executeTests = async () => {
    if (!activeTask?.runId || testRunning || agentBusyAction) return;
    setTestRunning(true);
    setTestError(null);
    try {
      const result = activeTask.validationChecks.length
        ? await runTests(activeTask.runId)
        : await runTests(activeTask.runId, testKind, testTarget.trim());
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
    setPatchRationale(null);
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

  const draftActivePatch = async () => {
    if (!activeTask?.runId || patchRunning) return;
    setPatchRunning(true);
    setPatchError(null);
    setPatchApplied(null);
    try {
      const preview = await draftPatch(activeTask.runId, { context_paths: patchPath.trim() ? [patchPath.trim()] : [] });
      setPatchPreviewResult(preview);
      setPatchRationale(preview.rationale);
      const events = await listRunEvents(activeTask.runId);
      setRunEvents(events.items);
    } catch (error) {
      setPatchError(error instanceof Error ? error.message : t("patch.draftError"));
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

  const requestPublishApproval = async (type: "push" | "pr") => {
    if (!activeTask || publishRunning) return;
    setPublishRunning(true);
    setPublishError(null);
    try {
      const summary = type === "push" ? t("publish.pushApprovalSummary") : t("publish.prApprovalSummary");
      const approval = await requestApproval(activeTask.id, type, summary);
      setApprovals((current) => [approval, ...current.filter((item) => item.id !== approval.id)]);
    } catch (error) {
      setPublishError(error instanceof Error ? error.message : t("publish.approvalRequestError"));
    } finally {
      setPublishRunning(false);
    }
  };

  const approvePublish = async (type: "push" | "pr") => {
    const approval = approvals.find((item) => item.type === type && item.status === "pending");
    if (!approval || publishRunning) return;
    setPublishRunning(true);
    setPublishError(null);
    try {
      const decided = await decideApproval(approval.id, "approve");
      setApprovals((current) => current.map((item) => item.id === decided.id ? decided : item));
    } catch (error) {
      setPublishError(error instanceof Error ? error.message : t("publish.approvalDecisionError"));
    } finally {
      setPublishRunning(false);
    }
  };

  const refreshPublishState = async (taskId: string) => {
    setPublishState(await getPublishState(taskId));
  };

  const publishTaskBranch = async () => {
    if (!activeTask || publishRunning || !publishState?.commits.length) return;
    setPublishRunning(true);
    setPublishError(null);
    if (!pushKeyRef.current) pushKeyRef.current = crypto.randomUUID();
    try {
      await pushTaskBranch(activeTask.id, "origin", pushKeyRef.current);
      pushKeyRef.current = "";
      await refreshPublishState(activeTask.id);
      updateTaskFromApi(await getTask(activeTask.id));
    } catch (error) {
      setPublishError(error instanceof Error ? error.message : t("publish.pushError"));
    } finally {
      setPublishRunning(false);
    }
  };

  const previewTaskPullRequest = async () => {
    if (!activeTask || publishRunning) return;
    setPublishRunning(true);
    setPublishError(null);
    try {
      setPullRequestPreview(await previewPullRequest(activeTask.id, pullRequestTarget));
    } catch (error) {
      setPublishError(error instanceof Error ? error.message : t("publish.previewError"));
    } finally {
      setPublishRunning(false);
    }
  };

  const createTaskPullRequest = async () => {
    const preview = pullRequestPreview;
    if (!activeTask || !preview || publishRunning || !preview.ready) return;
    setPublishRunning(true);
    setPublishError(null);
    if (!pullRequestKeyRef.current) pullRequestKeyRef.current = crypto.randomUUID();
    try {
      await createPullRequest(activeTask.id, { title: preview.title, body: preview.body, target_branch: preview.target_branch, draft: true }, pullRequestKeyRef.current);
      pullRequestKeyRef.current = "";
      await refreshPublishState(activeTask.id);
      updateTaskFromApi(await getTask(activeTask.id));
    } catch (error) {
      setPublishError(error instanceof Error ? error.message : t("publish.createError"));
    } finally {
      setPublishRunning(false);
    }
  };

  const reconcileTaskPullRequest = async () => {
    if (!activeTask || publishRunning) return;
    setPublishRunning(true);
    setPublishError(null);
    try {
      await refreshPullRequest(activeTask.id);
      await refreshPublishState(activeTask.id);
      updateTaskFromApi(await getTask(activeTask.id));
    } catch (error) {
      setPublishError(error instanceof Error ? error.message : t("publish.refreshError"));
    } finally {
      setPublishRunning(false);
    }
  };

  const updateTaskFromApi = (task: AgentTask) => {
    const nextTask = taskFromApi(task);
    setTasks((current) => [nextTask, ...current.filter((item) => item.id !== nextTask.id)]);
  };

  const beginIteration = async () => {
    if (!activeTask || iterationRunning || agentBusyAction) return;
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
    if (!activeTask || iterationRunning || agentBusyAction) return;
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
    if (!approval || iterationRunning || agentBusyAction) return;
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
    if (!activeTask || iterationRunning || agentBusyAction) return;
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
    if (!activeTask || iterationRunning || (agentBusyAction && agentBusyAction !== "run_tests")) return;
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
    if (!activeTask || iterationRunning || agentBusyAction) return;
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
      activeTaskHasGit ? getTaskGitSnapshot(activeTask.id) : Promise.resolve(null), getWorkspaceSummary(), listApprovals(activeTask.id),
    ]);
    if (taskRef.current !== activeTask.id) return;
    updateTaskFromApi(task); setIterations(nextIterations.items); setRunEvents(events.items);
    setRepositories(repositoryList.items); setGitSnapshot(snapshot); setWorkspaceSummary(summary); setApprovals(approvalList.items);
    setTestResult(nextIterations.items.at(-1)?.test_result ?? null);
    setPatchPreviewResult(null); setPatchApplied(null); setCommitPreviewResult(null); setPatchExpectedHash(undefined);
  };

  const refreshApprovalTask = async (taskId: string) => {
    if (taskId === activeTask?.id) await refreshActiveTask();
    else updateTaskFromApi(await getTask(taskId));
    setWorkspaceSummary(await getWorkspaceSummary());
  };

  const prepareSourceChange = (file: SourceFile) => {
    setPatchPath(file.path); setPatchContent(file.content); setPatchExpectedHash(file.content_hash);
    setPatchOperation("update"); setPatchPreviewResult(null); setPatchApplied(null); setPatchError(null); setActiveNav("runs");
  };

  const openFailure = (path: string, line: number) => {
    if (!activeTask?.repositoryId) return;
    setSelectedRepositoryId(activeTask.repositoryId); setSourceLocation({ path, line }); setActiveNav("repositories");
  };

  const agentRevision = [activeTask?.updatedAt, iterations.map(item => item.updated_at + String(item.rolled_back)).join(), approvals.map(item => item.id + item.status).join(), publishState?.pull_request?.updated_at].join(":");
  const operationBusy = Boolean(isRunning || testRunning || validationSaving || patchRunning || gitRunning || iterationRunning || publishRunning || repositorySaving || confirmAction || agentBusyAction || approvalRunning);
  const filteredTasks = tasks.filter(task => taskFilter === "all" || task.status === taskFilter);
  const chatTrack: TaskTrack = chatComposingNew ? railTab : (activeTask?.track ?? railTab);
  const chatRepositoryId = !chatComposingNew && activeTask && !["done", "completed", "cancelled"].includes(activeTask.lifecycleStatus) ? activeTask.repositoryId ?? "" : selectedRepositoryId;
  const openChatRun = () => {
    setActiveNav("runs");
    void refreshActiveTask().catch(error => setIterationError(error instanceof Error ? error.message : t("common.error")));
  };

  return (
    <div className="app-shell" data-page={activeNav}>
      <Dialog open={repositoryDialogOpen} title={t("folder.openLocal")} onClose={() => { if (!repositorySaving) setRepositoryDialogOpen(false); }}>
        <form className="folder-dialog" onSubmit={event => { event.preventDefault(); saveRepository(); }}>
          <div className="folder-dialog-intro"><span className="folder-dialog-icon"><FolderOpen size={24} strokeWidth={1.5} /></span><p>{t("folder.dialogDescription")}</p></div>
          {isDesktopShell ? <button type="button" className="folder-browse-button" onClick={addRepository} disabled={repositorySaving}><FolderOpen size={17} />{t("folder.browseAction")}<ArrowUpRight size={15} /></button> : <p className="folder-browser-hint">{t("folder.browserHint")}</p>}
          <label className="dialog-label" htmlFor="repository-path">{t("folder.pathLabel")}</label>
          <div className="repository-path-row">
            <input id="repository-path" autoFocus placeholder={t("folder.pathPlaceholder")} value={repositoryPathInput} onChange={event => setRepositoryPathInput(event.target.value)} disabled={repositorySaving} aria-invalid={Boolean(repositoryError)} aria-describedby={repositoryError ? "folder-open-error" : undefined} />
          </div>
          {repositoryError ? <p id="folder-open-error" className="inline-error" role="alert">{repositoryError}</p> : null}
          {repositories.length ? <div className="folder-recents"><h3>{t("folder.recent")}</h3>{repositories.slice(0, 5).map(repository => <button type="button" className="folder-recent" key={repository.id} onClick={() => openRegisteredFolder(repository.id)} disabled={repositorySaving}><FolderOpen size={17} /><span><strong>{repository.name}</strong><small title={repository.path}>{repository.path}</small></span><ArrowUpRight size={14} /></button>)}</div> : null}
          {isDesktopShell ? <details className="folder-workspace-settings" open={Boolean(repositoryError)}><summary>{t("folder.workspaceSettings")}</summary><div className="desktop-runtime">
            <div className="desktop-runtime-row"><span>{t("desktop.workspaceRoot")}</span><code>{desktopRuntime?.workspaceRoot || t("common.none")}</code></div>
            <div className="desktop-runtime-actions">
              <button type="button" className="text-button" onClick={() => void changeWorkspaceRoot()} disabled={repositorySaving}><FolderSync size={13} /> {t("desktop.changeWorkspace")}</button>
              <button type="button" className="text-button" onClick={() => void desktopBridge?.openLog()}>{t("desktop.openLog")}</button>
            </div>
            <p className="desktop-runtime-hint">{t("desktop.workspaceHint")}</p>
          </div></details> : null}
          <div className="dialog-actions"><button type="button" className="small-action small-action--muted" disabled={repositorySaving} onClick={() => setRepositoryDialogOpen(false)}>{t("cancel")}</button><button className="small-action folder-confirm" disabled={repositorySaving || !repositoryPathInput.trim()}>{repositorySaving ? t("folder.opening") : t("folder.openAction")}</button></div>
        </form>
      </Dialog>
      <Dialog open={confirmAction !== null} title={confirmAction === "delete" ? t("patch.delete") : t("cancel")} onClose={() => setConfirmAction(null)}>
        <p>{confirmAction === "delete" ? t("patch.deleteConfirm") : t("cancelTaskConfirm")}</p>
        {confirmAction === "delete" ? <pre>{patchPreviewResult?.files.join("\n")}</pre> : null}
        <div className="dialog-actions"><button className="small-action small-action--muted" onClick={() => setConfirmAction(null)}>{t("common.back")}</button><button className="small-action" onClick={() => confirmAction === "delete" ? void applyActivePatch(true) : void changeTaskState("cancel", true)}>{t("common.confirm")}</button></div>
      </Dialog>
      <aside className={`sidebar rail ${sidebarOpen ? "sidebar--open" : ""}`}>
        <div className="rail-brand"><Code2 size={19} /><span>Coding Agent</span></div>
        <div className="rail-head">
          <div className="rail-tabs" role="tablist" aria-label={t("workspace")}>
            <button role="tab" aria-selected={railTab === "cowork"} className={railTab === "cowork" ? "is-active" : ""} onClick={() => { if (railTab !== "cowork") { setRailTab("cowork"); startNewChatSession(); } }}>
              <ListChecks size={14} /> {t("rail.cowork")}
            </button>
            <button role="tab" aria-selected={railTab === "code"} className={railTab === "code" ? "is-active" : ""} onClick={() => { if (railTab !== "code") { setRailTab("code"); startNewChatSession(); } }}>
              <Code2 size={14} /> {t("rail.code")}
            </button>
          </div>
          <button className="icon-button mobile-close" aria-label={t("closeNavigation")} onClick={() => setSidebarOpen(false)}>
            <X size={17} />
          </button>
        </div>

        <button className="rail-new" onClick={() => { startNewChatSession(); setSidebarOpen(false); }}>
          <Plus size={15} /> {t("rail.new")}
        </button>
        <button className="rail-open-folder" onClick={addRepository} disabled={repositorySaving}><FolderOpen size={16} />{repositorySaving ? t("folder.opening") : t("folder.openLocal")}</button>

        <nav className="rail-nav" aria-label={t("primaryNavigation")}>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button key={item.key} className={`rail-nav-item ${activeNav === item.key ? "is-active" : ""}`} onClick={() => { setActiveNav(item.key); setSidebarOpen(false); }}>
                <Icon size={16} strokeWidth={1.8} />
                <span>{t(`nav.${item.key}`)}</span>
                {item.key === "approvals" && approvalCount ? <span className="nav-count">{approvalCount}</span> : null}
              </button>
            );
          })}
        </nav>

        <div className="rail-section"><span>{railTab === "cowork" ? t("rail.chats") : t("rail.folders")}</span>{railTab === "code" ? <button className="icon-button" aria-label={t("folder.openLocal")} onClick={addRepository} disabled={repositorySaving}><Plus size={15} /></button> : null}</div>
        <div className="rail-conversations">
          {railTab === "cowork" ? (
            coworkTasks.length ? (
              coworkTasks.map((task) => <RailSession key={task.id} task={task} active={task.id === activeTaskId && !chatComposingNew} onClick={() => { selectChatSession(task.id); setSidebarOpen(false); }} />)
            ) : <p className="rail-empty">{t("chat.noSessions")}</p>
          ) : (
            railGroups.length ? (
              railGroups.map(({ id, name, path, rows }) => (
                <div className="rail-group" key={id}>
                  <details className="rail-folder-group" open><summary title={path || name}><FolderOpen size={14} /><span>{name}</span><span className="rail-folder-count">{rows.length}</span></summary><div className="rail-folder-sessions">
                  {rows.map((task) => <RailSession key={task.id} task={task} active={task.id === activeTaskId && !chatComposingNew} onClick={() => { selectChatSession(task.id); setSidebarOpen(false); }} />)}
                  {path ? <button className="rail-folder-new" onClick={() => openRegisteredFolder(id)}><Plus size={13} />{t("rail.newInFolder")}</button> : null}
                  </div></details>
                </div>
              ))
            ) : <p className="rail-empty">{t("rail.emptyFolders")}</p>
          )}
        </div>

        <div className="rail-footer">
          <div className="rail-local"><span className="pulse-dot" /> {t("localExecution")}</div>
          <div className="rail-account-wrap">
            {accountMenuOpen ? (
              <>
                <button className="rail-backdrop" aria-hidden tabIndex={-1} onClick={() => setAccountMenuOpen(false)} />
                <div className="rail-menu" role="menu">
                  <label className="preference-control"><Languages size={14} /><select value={language} onChange={(event) => setLanguage(event.target.value as Language)} aria-label={t("language")}><option value="zh-CN">{t("chinese")}</option><option value="en-US">{t("english")}</option></select></label>
                  <label className="preference-control"><span className="preference-theme-icon">{theme === "light" ? <Sun size={14} /> : theme === "dark" ? <Moon size={14} /> : <Monitor size={14} />}</span><select value={theme} onChange={(event) => setTheme(event.target.value as ThemeMode)} aria-label={t("theme")}><option value="system">{t("system")}</option><option value="light">{t("light")}</option><option value="dark">{t("dark")}</option></select></label>
                  <button className="rail-menu-item" disabled><Settings2 size={14} /> {t("settings")}</button>
                </div>
              </>
            ) : null}
            <button className="rail-account" aria-label={t("rail.account")} aria-expanded={accountMenuOpen} onClick={() => setAccountMenuOpen((open) => !open)}>
              <div className="avatar"><Monitor size={16} /></div>
              <div className="user-copy"><strong>{t("rail.localAccount")}</strong><span>{t("rail.preferences")}</span></div>
              <MoreHorizontal size={16} />
            </button>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="topbar-left">
          <button className="icon-button menu-toggle" aria-label={t("openNavigation")} onClick={() => setSidebarOpen(true)}><Menu size={18} /></button>
            <div className="breadcrumbs"><span>{t("workspace")}</span><span className="breadcrumb-slash">/</span><strong>{activeNav === "chat" ? t(`rail.${chatTrack}`) : pageTitle}</strong></div>
          </div>
          <div className="topbar-actions">
            <div className={`connection-status connection-status--${apiStatus}`}><span className="pulse-dot" /> {apiStatus === "connected" ? t("apiConnected") : apiStatus === "offline" ? t("apiOffline") : t("checkingApi")}</div>
            <button className="topbar-folder" onClick={addRepository} disabled={repositorySaving}><FolderOpen size={15} /><span>{t("folder.openLocal")}</span></button>
          </div>
        </header>

        <div className="page-wrap" data-view={activeNav}>
          <ChatPanel
            goal={chatComposingNew || chatTrack === "cowork" ? null : (activeTask?.title ?? null)}
            plan={chatComposingNew || chatTrack === "cowork" ? undefined : (activeTask?.plan ?? undefined)}
            events={chatComposingNew ? [] : runEvents}
            loopRunning={chatTrack === "code" && loopRunning}
            sending={chatSending}
            status={chatComposingNew ? null : (activeTask?.lifecycleStatus ?? null)}
            hasActiveRun={!chatComposingNew && Boolean(activeTask?.runId)}
            track={chatTrack}
            repositories={repositories}
            selectedRepositoryId={chatRepositoryId}
            onSelectRepository={setSelectedRepositoryId}
            onImportFolder={addRepository}
            onBrowseFolder={() => { setSelectedRepositoryId(chatRepositoryId); setActiveNav("repositories"); }}
            onSend={sendChatMessage}
            onStop={stopChatLoop}
            onOpenPublish={openChatRun}
            reviewPatch={!chatComposingNew && activeTask?.mode === "conservative" && chatAction === "review_patch" && !loopRunning}
            onReviewPatch={openChatRun}
            error={chatError}
            mode={chatComposingNew || !activeTask ? chatMode : activeTask.mode}
            modeLocked={!chatComposingNew && Boolean(activeTask)}
            onModeChange={setChatMode}
            pending={chatComposingNew ? null : chatPending}
            onDecide={decideChatApproval}
            budget={chatComposingNew ? null : chatBudget}
          />
          {activeNav === "repositories" ? <RepositoryBrowser repositories={repositories} repositoryId={selectedRepositoryId} onRepositoryChange={(id) => { setSelectedRepositoryId(id); setSourceLocation(null); }} onOpenFolder={addRepository} onEdit={prepareSourceChange} canEdit={Boolean(activeTask?.repositoryId === selectedRepositoryId && activeTask.lifecycleStatus === "coding")} initialFile={sourceLocation} /> : null}
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

          {overviewError ? (
            <div className="overview-error-banner">
              <AsyncMessage tone="error" onRetry={() => void loadOverviewData()} retryLabel={t("common.retry")}>
                {overviewError}
              </AsyncMessage>
            </div>
          ) : null}

          <section className="command-panel" inert={Boolean(agentBusyAction) || approvalRunning}>
            <div className="section-heading"><div><div className="section-kicker"><Sparkles size={13} /> {t("startTask")}</div><h2>{t("whatShouldForgeWorkOn")}</h2></div><span className="shortcut-hint" title={t("shortcuts.taskPrompt")}><Command size={12} /> K</span></div>
            <div className="prompt-row">
              <div className="prompt-prefix"><span className="prompt-dot" /><span>forge</span><span className="prompt-arrow">›</span></div>
              <input ref={promptInputRef} value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") runTask(); }} placeholder={t("taskPromptPlaceholder")} aria-label={t("taskPromptLabel")} />
              <button className="run-button" onClick={() => void runTask()} disabled={!prompt.trim() || isRunning}><span>{isRunning ? t("running") : t("runTask")}</span>{isRunning ? <Activity size={15} className="spin" /> : <Send size={15} />}</button>
            </div>
            <div className="prompt-footer"><span><CircleDot size={12} /> {t("agentInspect")}</span><span className="prompt-footer-right"><LockKeyhole size={12} /> {t("localExecutionDetail")}</span></div>{taskError ? <div className="repo-error task-error">{taskError}</div> : null}
          </section>

          <div className="content-grid" inert={approvalRunning}>
            <section className="panel task-panel">
              <div className="panel-header"><div><div className="section-kicker">{t("inFlight")}</div><h2>{t("taskQueue")}</h2></div><button className="text-button" onClick={() => setActiveNav("tasks")}>{t("viewAll")} <ArrowUpRight size={14} /></button></div>
              <div className="task-filter"><label htmlFor="task-filter">{t("task.filter")}</label><select id="task-filter" value={taskFilter} onChange={event => setTaskFilter(event.target.value)}>{["all", "queued", "running", "review", "done", "failed", "paused", "cancelled"].map(status => <option value={status} key={status}>{status === "all" ? t("task.all") : t(status)}</option>)}</select></div>
              <div className="task-list">
                {overviewLoading && !tasks.length ? (
                  <div className="empty-operation queue-empty"><AsyncMessage tone="loading">{t("common.loading")}</AsyncMessage></div>
                ) : (
                  <>
                    {(activeNav === "tasks" ? filteredTasks : filteredTasks.slice(0, 4)).map((task) => <TaskRow key={task.id} task={task} active={task.id === activeTaskId} onClick={() => { if (!operationBusy) { setActiveTaskId(task.id); setActiveNav("runs"); } }} />)}
                    {!filteredTasks.length ? <div className="empty-operation queue-empty"><Zap size={17} /><span>{tasks.length ? t("task.noMatch") : t("noTasks")}</span></div> : null}
                  </>
                )}
              </div>
              <button className="add-task-button" onClick={() => promptInputRef.current?.focus()}><Plus size={15} /> {t("startAnotherTask")}</button>
            </section>

            <section className="panel stream-panel">
              <div className="panel-header"><div><div className="section-kicker">TRACE / {activeTask?.id.toUpperCase()}</div><h2>{t("executionStream")}</h2></div><span className="live-badge"><span className="pulse-dot" /> {t("liveEvents")}</span></div>
              {activeTask?.requiresRecovery ? <div className="recovery-note" role="status"><AlertTriangle size={14} /><span>{t("recovery.title")}</span><button className="small-action" onClick={() => void confirmRecovery()} disabled={iterationRunning || Boolean(agentBusyAction)}>{t("recovery.action")}</button></div> : null}
              {activeTask ? <div className="iteration-bar"><div className="iteration-status"><strong>{t("iteration")} {activeTask.currentIteration || iterations.length || 0}</strong><span>{activeTask.nextAction ?? activeTask.plan?.objective ?? t("currentCodingLoop")}</span></div><div className="iteration-actions">{activeTask.status === "failed" ? <button className="small-action" onClick={() => void beginRepair()} disabled={iterationRunning || Boolean(agentBusyAction)}>{t("repair")}</button> : activeTask.status === "paused" ? <button className="small-action" onClick={() => void changeTaskState("resume")} disabled={iterationRunning || Boolean(agentBusyAction)}>{t("resume")}</button> : (activeTask.status === "review" && !iterations.length || activeTask.lifecycleStatus === "coding" && Boolean(iterations.at(-1)?.rolled_back)) ? planApproval?.status === "approved" ? <button className="small-action" onClick={() => void beginIteration()} disabled={iterationRunning || Boolean(agentBusyAction) || Boolean(activeTaskHasGit && !activeTask.taskBranch)} title={activeTaskHasGit && !activeTask.taskBranch ? t("createBranchBeforeCommit") : undefined}>{t("startIteration")}</button> : <button className="small-action" onClick={() => void requestPlanApproval()} disabled={iterationRunning || Boolean(agentBusyAction)}>{planApproval ? t("awaitingApproval") : t("requestPlanApproval")}</button> : null}{planApproval?.status === "pending" ? <button className="small-action small-action--muted" onClick={() => void approvePlan()} disabled={iterationRunning || Boolean(agentBusyAction)}>{t("approvePlan")}</button> : null}{["coding", "testing", "running"].includes(activeTask.status) ? <button className="small-action small-action--muted" onClick={() => void changeTaskState("pause")} disabled={iterationRunning || Boolean(agentBusyAction && agentBusyAction !== "run_tests")}>{t("pause")}</button> : null}{!["cancelled", "done"].includes(activeTask.status) ? <button className="text-button" onClick={() => void changeTaskState("cancel")} disabled={iterationRunning || Boolean(agentBusyAction && agentBusyAction !== "run_tests")}>{t("cancel")}</button> : null}</div></div> : null}
              {iterationError ? <div className="inline-error">{iterationError}</div> : null}
              {activeTask?.plan ? <div className="plan-list"><div className="plan-label">{t("plan")} / {activeTask.plan.steps.length} {t("steps")}</div>{activeTask.plan.steps.map((step, index) => <div className="plan-step" key={step.id}><span className={`plan-step-index ${index === 0 ? "plan-step-index--active" : ""}`}>{index + 1}</span><div className="plan-step-copy"><strong>{step.title}</strong><span>{step.description}</span></div><span className={`plan-risk plan-risk--${step.risk}`}>{t(`risk.${step.risk}`)}</span></div>)}</div> : null}
              {iterations.length ? <div className="iteration-history"><div className="plan-label">{t("iterations")} / {iterations.length}</div>{iterations.map((iteration) => <div className="iteration-row" key={iteration.id}><span className={`iteration-dot iteration-dot--${iteration.status}`} /><div className="iteration-row-copy"><strong>#{iteration.number} {t(`iterationStatus.${iteration.status}`)}</strong><span>{iteration.goal}</span>{iteration.failure_summary ? <small>{iteration.failure_summary}</small> : null}</div>{iteration.test_result?.failed_tests?.[0] ? <span className="failure-location">{iteration.test_result.failed_tests[0].path ?? t("testOutput")}{iteration.test_result.failed_tests[0].line ? `:${iteration.test_result.failed_tests[0].line}` : ""}</span> : null}</div>)}</div> : null}
              <div className="stream-list">
                {displayedStreamEvents.map((event) => {
                  const Icon = iconForEvent(event.icon);
                  return <div className={`stream-event stream-event--${event.status}`} key={event.id}><div className="stream-time">{event.time}</div><div className="stream-marker"><Icon size={14} /></div><div className="stream-copy"><strong>{event.title}</strong><span>{event.detail}</span></div>{event.status === "done" ? <Check size={15} className="stream-check" /> : event.status === "active" ? <span className="stream-spinner" /> : <Clock3 size={15} className="stream-wait" />}</div>;
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

          {activeTask?.runId ? <AgentPanel key={activeTask.id} taskId={activeTask.id} revision={agentRevision} testKind={activeTask.validationChecks.length ? "auto" : testKind} testTarget={activeTask.validationChecks.length ? "" : testTarget} disabled={operationBusy} hidden={activeNav !== "runs"} onAdvanced={refreshActiveTask} onBusyChange={setAgentBusyAction} /> : null}

          <section className="operations-grid" inert={Boolean(agentBusyAction) || approvalRunning}>
            <section className="panel test-panel">
              <div className="panel-header"><div><div className="section-kicker"><PackageCheck size={13} /> {t("validation")}</div><h2>{t("testConsole")}</h2></div><button className="small-action" onClick={() => void executeTests()} disabled={!activeTask?.runId || operationBusy || !iterations.length || Boolean(iterations.at(-1)?.rolled_back) || ["paused", "cancelled", "done"].includes(activeTask.status)}>{testRunning ? <><Activity size={13} className="spin" /> {t("running")}</> : <><TerminalSquare size={13} /> {t(activeTask?.validationChecks.length ? "validation.next" : "runTests")}</>}</button></div>
              {activeTask?.runId ? <ValidationChecklist key={activeTask.id} taskId={activeTask.id} checks={activeTask.validationChecks} currentIteration={activeTask.currentIteration} revision={agentRevision} hidden={activeNav !== "runs"} disabled={operationBusy || ["paused", "cancelled", "done"].includes(activeTask.status)} onSaved={updateTaskFromApi} onBusyChange={setValidationSaving} /> : null}
              {!activeTask?.validationChecks.length ? <div className="validation-options"><label>{t("validation.kind")}<select value={testKind} onChange={event => setTestKind(event.target.value as TestKind)} disabled={testRunning}>{["auto", "pytest", "frontend", "static"].map(kind => <option value={kind} key={kind}>{t("validation." + kind)}</option>)}</select></label><input value={testTarget} onChange={event => setTestTarget(event.target.value)} aria-label={t("validation.target")} placeholder={t("validation.target")} disabled={testRunning} /></div> : null}
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
                <div className="patch-form-actions"><span>{patchPath.trim() ? `${t("patch.draftContext")}：${patchPath.trim()}` : t("updateOnly")}</span><div className="patch-form-buttons"><button className="small-action small-action--muted" onClick={() => void draftActivePatch()} disabled={!activeTask?.runId || patchRunning}>{patchRunning ? t("patch.drafting") : t("patch.draft")}</button><button className="small-action" onClick={() => void previewActivePatch()} disabled={!activeTask?.runId || !patchPath.trim() || patchRunning}>{patchRunning ? t("working") : t("previewDiff")}</button></div></div>
              </div>
              {patchPreviewResult ? <div className="patch-result">{patchRationale ? <div className="patch-rationale"><strong>{t("patch.rationale")}</strong> {patchRationale}</div> : null}<div className="patch-summary"><span>{patchPreviewResult.files.length} {t("file")}</span><span className="diff-add">+{patchPreviewResult.additions}</span><span className="diff-del">-{patchPreviewResult.deletions}</span><span>{patchPreviewResult.bytes_changed} {t("bytes")}</span></div><pre className="diff-view">{patchPreviewResult.diff || t("noChangesDetected")}</pre><div className="patch-result-actions"><span>{t("reviewUnifiedDiff")}</span><div className="patch-result-buttons"><button className="small-action small-action--muted" onClick={cancelPatchPreview} disabled={patchRunning}>{t("cancel")}</button><button className="small-action" onClick={() => void applyActivePatch()} disabled={patchRunning || !patchPreviewResult.diff}>{t("applyCheckpoint")}</button></div></div></div> : <div className="empty-operation"><FileCode2 size={17} /><span>{t("previewBeforeRepository")}</span></div>}
              {patchApplied ? <div className="success-note"><Check size={14} /> {patchApplied}</div> : null}
              {patchError ? <div className="inline-error">{patchError}</div> : null}
            </section>
          </section>

          {activeTaskHasGit ? <><section className="operations-grid git-grid" inert={Boolean(agentBusyAction) || approvalRunning}>
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

          <section className="operations-grid publish-grid" inert={Boolean(agentBusyAction) || approvalRunning}>
            <section className="panel publish-panel">
              <div className="panel-header"><div><div className="section-kicker"><Rocket size={13} /> {t("publish.kicker")}</div><h2>{t("publish.title")}</h2></div><span className="patch-safety"><LockKeyhole size={12} /> {t("publish.gated")}</span></div>
              <div className="publish-target">{publishState?.remote_url ? <div><span>{t("publish.remote")}</span><code>{publishState.remote_url}</code></div> : <div className="publish-muted">{t("publish.noRemote")}</div>}{remoteTarget ? <div><span>{t("publish.defaultBranch")}</span><code>{remoteTarget.host} · {remoteTarget.default_branch}</code></div> : null}</div>
              <div className="publish-row">
                <div className="publish-row-copy"><strong>{t("publish.pushTitle")}</strong><span>{publishState?.commits.length ? `${publishState.commits.length} ${t("publish.commits")}` : t("publish.noCommits")}</span></div>
                {pushApproval?.status === "approved" ? <button className="small-action" onClick={() => void publishTaskBranch()} disabled={!publishState?.commits.length || publishRunning}>{publishRunning ? t("working") : t("publish.push")}</button> : <button className="small-action small-action--muted" onClick={() => void requestPublishApproval("push")} disabled={publishRunning}>{pushApproval ? t("awaitingApproval") : t("publish.requestPushApproval")}</button>}
                {pushApproval?.status === "pending" ? <button className="small-action" onClick={() => void approvePublish("push")} disabled={publishRunning}>{t("approve")}</button> : null}
              </div>
              {publishState?.remote_branches.length ? <div className="publish-list">{publishState.remote_branches.map((branch) => <div className="publish-item" key={branch.id}><GitBranch size={13} /><span>{branch.branch}</span><code>{branch.pushed_sha.slice(0, 10)}</code></div>)}</div> : null}
              <div className="publish-row">
                <div className="publish-row-copy"><strong>{t("publish.prTitle")}</strong><span>{publishState?.pull_request ? `#${publishState.pull_request.number} · ${publishState.pull_request.state} → ${publishState.pull_request.target_branch}` : t("publish.prHint")}</span></div>
                {publishState?.pull_request ? <button className="small-action small-action--muted" onClick={() => void reconcileTaskPullRequest()} disabled={publishRunning}>{t("publish.reconcile")}</button> : <>
                  <button className="small-action small-action--muted" onClick={() => void previewTaskPullRequest()} disabled={!publishState?.remote_branches.length || publishRunning}>{t("publish.previewPr")}</button>
                  {pullRequestPreview ? (pullRequestApproval?.status === "approved" ? <button className="small-action" onClick={() => void createTaskPullRequest()} disabled={!pullRequestPreview.ready || publishRunning}>{publishRunning ? t("working") : t("publish.createPr")}</button> : <><button className="small-action small-action--muted" onClick={() => void requestPublishApproval("pr")} disabled={publishRunning}>{pullRequestApproval ? t("awaitingApproval") : t("publish.requestPrApproval")}</button>{pullRequestApproval?.status === "pending" ? <button className="small-action" onClick={() => void approvePublish("pr")} disabled={publishRunning}>{t("approve")}</button> : null}</>) : null}
                </>}
              </div>
              {pullRequestPreview ? <div className="patch-result"><div className="patch-summary"><span>{pullRequestPreview.files.length} {t("files")}</span><span>{pullRequestPreview.source_branch} → {pullRequestPreview.target_branch}</span><span>{pullRequestPreview.ready ? t("ready") : t("publish.notReady")}</span></div><pre className="diff-view">{pullRequestPreview.body}</pre></div> : null}
              {publishState?.pull_request ? <div className="success-note"><Check size={14} /><a className="publish-link" href={publishState.pull_request.html_url} target="_blank" rel="noreferrer">{t("publish.openPr")}</a><ArrowUpRight size={13} /></div> : null}
              {publishState?.remote_calls.length ? <div className="remote-call-log">
                <div className="plan-label">{t("publish.remoteCalls")} / {publishState.remote_calls.length}</div>
                {publishState.remote_calls.slice(-5).reverse().map((call) => <div className={`remote-call remote-call--${call.outcome}`} key={call.id}>
                  <span className="remote-call-op">{t("publish.op." + call.operation)}</span>
                  <span className="remote-call-meta">{call.outcome === "error" ? (call.error_code ?? t("publish.callError")) : (call.status_code ?? "—")} · {call.attempts}× · {call.duration_ms}ms</span>
                </div>)}
              </div> : null}
              {publishError ? <div className="inline-error">{publishError}</div> : null}
            </section>
          </section>

          </> : null}

          {activeTask?.runId && activeNav !== "repositories" ? <RunHistory runId={activeTask.runId} revision={iterations.map(item => item.updated_at + String(item.rolled_back)).join()} onRestored={refreshActiveTask} disabled={testRunning || patchRunning || gitRunning || Boolean(agentBusyAction) || ["cancelled", "done"].includes(activeTask.status)} /> : null}

          <section className="lower-grid" inert={Boolean(agentBusyAction)}>
            <section className="panel repo-panel"><div className="panel-header"><div><div className="section-kicker">{t("codebasePulse")}</div><h2>{t("repositories")}</h2></div><button className="small-action" onClick={() => void addRepository()}><Plus size={13} /> {t("add")}</button></div>{overviewLoading && !repositories.length ? <div className="repo-empty"><AsyncMessage tone="loading">{t("common.loading")}</AsyncMessage></div> : repositories.length ? repositories.map((repository, index) => <RepoRow key={repository.id} repository={repository} tone={["amber", "cyan", "lime"][index % 3]} t={t} />) : <div className="repo-empty"><GitBranch size={18} /><span>{t("noRepositoryConnected")}</span><button className="text-button" onClick={() => void addRepository()}>{t("addRepository")} <ArrowUpRight size={13} /></button></div>}{repositoryError ? <div className="repo-error">{repositoryError}</div> : null}</section>
            <ApprovalPanel compact={activeNav !== "approvals"} revision={agentRevision} disabled={operationBusy} tasks={tasks} onOpenTask={taskId => { setActiveTaskId(taskId); setActiveNav("runs"); }} onOpenCenter={() => setActiveNav("approvals")} onChanged={refreshApprovalTask} onBusyChange={setApprovalRunning} onPendingCount={setApprovalCount} />
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

function RailSession({ task, active, onClick }: { task: Task; active: boolean; onClick: () => void }) {
  return (
    <button className={`chat-session ${active ? "is-active" : ""}`} onClick={onClick} title={task.title}>
      <span className={`chat-session-dot chat-session-dot--${task.track === "cowork" ? "cowork" : task.status}`} />
      <span className="chat-session-title">{task.title}</span>
    </button>
  );
}

function RepoRow({ repository, tone, t }: { repository: Repository; tone: string; t: (key: string) => string }) {
  const language = Object.keys(repository.languages)[0] ?? t("unknown");
  const status = repository.changed_files ? `${repository.changed_files} ${t("changes")}` : t("synced");
  return <div className="repo-row"><div className={`repo-icon repo-icon--${tone}`}><Box size={15} /></div><div className="repo-copy"><strong>{repository.name}</strong><span>{language} · {repository.branch}</span></div><span className={`repo-status repo-status--${tone}`}><span className="mini-dot" />{status}</span></div>;
}

export default App;
