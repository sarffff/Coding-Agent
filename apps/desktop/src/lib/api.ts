const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type ApiHealth = {
  status: "ok";
  service: string;
  timestamp: string;
};

export type WorkspaceSummary = {
  repositories: number;
  active_tasks: number;
  pending_approvals: number;
  sandbox_status: "ready" | "degraded";
};

export type Repository = {
  id: string;
  name: string;
  path: string;
  branch: string;
  head: string | null;
  last_commit: string | null;
  changed_files: number;
  languages: Record<string, number>;
  package_manager: string | null;
  registered_at: string;
};

export type RepositoryValidation = {
  valid: boolean;
  path: string;
  git_root: string | null;
  reason: string | null;
};

export type TreeEntry = {
  path: string;
  name: string;
  kind: "file" | "directory";
  size: number | null;
  language: string | null;
};

export type SearchMatch = {
  path: string;
  line: number;
  column: number;
  text: string;
};

export type TaskPlanStep = {
  id: string;
  title: string;
  description: string;
  files: string[];
  risk: "low" | "medium" | "high";
  verification: string[];
};

export type AgentTask = {
  id: string;
  run_id: string;
  goal: string;
  repository_id: string | null;
  status: "queued" | "planning" | "coding" | "testing" | "repairing" | "running" | "review" | "awaiting_approval" | "ready_for_pr" | "pr_created" | "completed" | "done" | "failed" | "cancelled" | "paused";
  plan: {
    objective: string;
    repository_summary: string;
    steps: TaskPlanStep[];
  } | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  current_iteration: number;
  retry_count: number;
  next_action: string | null;
};

export type TestFailure = {
  path: string | null;
  line: number | null;
  test_name: string | null;
  message: string;
};

export type TaskIteration = {
  id: string;
  task_id: string;
  run_id: string;
  number: number;
  goal: string;
  status: "coding" | "testing" | "passed" | "failed" | "cancelled";
  changed_files: string[];
  patch_id: string | null;
  checkpoint_id: string | null;
  test_result: TestRunResult | null;
  failure_summary: string | null;
  created_at: string;
  updated_at: string;
};

export type TestRunResult = {
  run_id: string;
  kind: "auto" | "pytest" | "frontend";
  command: string[];
  status: "passed" | "failed" | "timed_out" | "not_found" | "blocked";
  exit_code: number | null;
  duration_ms: number;
  stdout: string;
  stderr: string;
  output_truncated: boolean;
  failed_tests: TestFailure[];
};

export type PatchFileInput = {
  path: string;
  content?: string;
  operation?: "create" | "update" | "delete";
  expected_hash?: string;
};

export type PatchPreview = {
  patch_id: string;
  repository_id: string;
  files: string[];
  diff: string;
  additions: number;
  deletions: number;
  bytes_changed: number;
  requires_delete_confirmation: boolean;
};

export type GitSnapshot = {
  branch: string;
  head: string;
  changed_files: string[];
  clean: boolean;
};

export type CommitPreview = GitSnapshot & {
  diff: string;
  commit_message: string;
  scope_hash: string;
  ready: boolean;
};

export type Approval = {
  id: string;
  task_id: string;
  run_id: string;
  type: "plan" | "write" | "push" | "pr";
  status: "pending" | "approved" | "rejected" | "request_changes" | "cancelled" | "expired";
  summary: string;
  scope_hash: string;
  reason: string | null;
  requested_at: string;
  expires_at: string;
  decided_at: string | null;
  decided_by: string | null;
};

export type AuditEvent = {
  id: string;
  action: string;
  status: "started" | "succeeded" | "failed";
  run_id: string | null;
  task_id: string | null;
  trace_id: string | null;
  summary: string;
  details: Record<string, unknown>;
  created_at: string;
};

export class ApiRequestError extends Error {
  constructor(public code: string, message: string, public traceId?: string) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { code?: string; message?: string; trace_id?: string } | null;
    throw new ApiRequestError(payload?.code ?? "API_ERROR", payload?.message ?? `Request failed: ${response.status}`, payload?.trace_id);
  }
  return response.json() as Promise<T>;
}

export function getApiHealth(signal?: AbortSignal): Promise<ApiHealth> {
  return request<ApiHealth>("/health", { signal });
}

export function getWorkspaceSummary(signal?: AbortSignal): Promise<WorkspaceSummary> {
  return request<WorkspaceSummary>("/api/v1/workspace/summary", { signal });
}

export function listRepositories(signal?: AbortSignal): Promise<{ items: Repository[]; total: number }> {
  return request<{ items: Repository[]; total: number }>("/api/v1/repositories", { signal });
}

export function validateRepository(path: string): Promise<RepositoryValidation> {
  return request<RepositoryValidation>("/api/v1/repositories/validate", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function registerRepository(path: string, name?: string): Promise<Repository> {
  return request<Repository>("/api/v1/repositories", {
    method: "POST",
    body: JSON.stringify({ path, name: name || undefined }),
  });
}

export function getRepositoryTree(repositoryId: string, signal?: AbortSignal): Promise<{ items: TreeEntry[]; truncated: boolean }> {
  return request<{ items: TreeEntry[]; truncated: boolean }>(`/api/v1/repositories/${repositoryId}/tree`, { signal });
}

export function createTask(goal: string, repositoryId?: string): Promise<AgentTask> {
  return request<AgentTask>("/api/v1/tasks", {
    method: "POST",
    body: JSON.stringify({ goal, repository_id: repositoryId }),
  });
}

export function startIteration(taskId: string, goal?: string): Promise<TaskIteration> {
  return request<TaskIteration>(`/api/v1/tasks/${taskId}/iterations`, {
    method: "POST",
    body: JSON.stringify({ goal }),
  });
}

export function listIterations(taskId: string, signal?: AbortSignal): Promise<{ items: TaskIteration[]; total: number }> {
  return request<{ items: TaskIteration[]; total: number }>(`/api/v1/tasks/${taskId}/iterations`, { signal });
}

export function startRepair(taskId: string, feedback?: string): Promise<TaskIteration> {
  return request<TaskIteration>(`/api/v1/tasks/${taskId}/repairs`, {
    method: "POST",
    body: JSON.stringify({ feedback }),
  });
}

export function pauseTask(taskId: string): Promise<{ task: AgentTask; action: "paused" }> {
  return request<{ task: AgentTask; action: "paused" }>(`/api/v1/tasks/${taskId}/pause`, { method: "POST" });
}

export function resumeTask(taskId: string): Promise<{ task: AgentTask; action: "resumed" }> {
  return request<{ task: AgentTask; action: "resumed" }>(`/api/v1/tasks/${taskId}/resume`, { method: "POST" });
}

export function cancelTask(taskId: string): Promise<{ task: AgentTask; action: "cancelled" }> {
  return request<{ task: AgentTask; action: "cancelled" }>(`/api/v1/tasks/${taskId}/cancel`, { method: "POST" });
}

export function createTaskBranch(taskId: string, name: string, allowDirty = false): Promise<GitSnapshot> {
  return request<GitSnapshot>(`/api/v1/tasks/${taskId}/branch`, {
    method: "POST",
    body: JSON.stringify({ name, allow_dirty: allowDirty }),
  });
}

export function getTaskGitSnapshot(taskId: string, signal?: AbortSignal): Promise<GitSnapshot> {
  return request<GitSnapshot>(`/api/v1/tasks/${taskId}/git`, { signal });
}

export function previewCommit(taskId: string, message: string): Promise<CommitPreview> {
  return request<CommitPreview>(`/api/v1/tasks/${taskId}/commits/preview`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function createCommit(taskId: string, message: string): Promise<GitSnapshot> {
  return request<GitSnapshot>(`/api/v1/tasks/${taskId}/commits`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function listApprovals(taskId: string, signal?: AbortSignal): Promise<{ items: Approval[]; total: number }> {
  return request<{ items: Approval[]; total: number }>(`/api/v1/tasks/${taskId}/approvals`, { signal });
}

export function requestApproval(taskId: string, type: Approval["type"], summary: string, scopeHash?: string): Promise<Approval> {
  return request<Approval>(`/api/v1/tasks/${taskId}/approvals`, {
    method: "POST",
    body: JSON.stringify({ type, summary, scope_hash: scopeHash }),
  });
}

export function decideApproval(approvalId: string, decision: "approve" | "reject" | "request_changes" | "cancel", reason?: string): Promise<Approval> {
  return request<Approval>(`/api/v1/approvals/${approvalId}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision, reason }),
  });
}

export function listTasks(signal?: AbortSignal): Promise<{ items: AgentTask[]; total: number }> {
  return request<{ items: AgentTask[]; total: number }>("/api/v1/tasks", { signal });
}

export function getTask(taskId: string, signal?: AbortSignal): Promise<AgentTask> {
  return request<AgentTask>(`/api/v1/tasks/${taskId}`, { signal });
}

export function listRunEvents(runId: string, signal?: AbortSignal): Promise<{ items: AuditEvent[]; total: number }> {
  return request<{ items: AuditEvent[]; total: number }>(`/api/v1/runs/${runId}/events`, { signal });
}

export function runTests(runId: string, kind: "auto" | "pytest" | "frontend" = "auto"): Promise<TestRunResult> {
  return request<TestRunResult>(`/api/v1/runs/${runId}/tests`, {
    method: "POST",
    body: JSON.stringify({ kind }),
  });
}

export function previewPatch(runId: string, files: PatchFileInput[], confirmDelete = false): Promise<PatchPreview> {
  return request<PatchPreview>(`/api/v1/runs/${runId}/patches/preview`, {
    method: "POST",
    body: JSON.stringify({ files, confirm_delete: confirmDelete }),
  });
}

export function applyPatch(runId: string, patchId: string, confirm = false): Promise<{ patch_id: string; checkpoint: { id: string; files: string[] }; applied_files: string[] }> {
  return request<{ patch_id: string; checkpoint: { id: string; files: string[] }; applied_files: string[] }>(`/api/v1/runs/${runId}/patches/apply`, {
    method: "POST",
    body: JSON.stringify({ patch_id: patchId, confirm }),
  });
}
