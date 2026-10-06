import type { ApiHealth, WorkspaceSummary, Repository, RepositoryValidation, TreeEntry, SearchMatch, TaskPlanStep, AgentTask, TestFailure, TaskIteration, TestKind, TestRunResult, PatchFileInput, PatchPreview, GitSnapshot, CommitPreview, Approval, AuditEvent, SourceFile, RepositoryContext, RunCheckpoint, TestHistoryItem } from "@coding-agent/types";
export type { ApiHealth, WorkspaceSummary, Repository, RepositoryValidation, TreeEntry, SearchMatch, TaskPlanStep, AgentTask, TestFailure, TaskIteration, TestKind, TestRunResult, PatchFileInput, PatchPreview, GitSnapshot, CommitPreview, Approval, AuditEvent, SourceFile, RepositoryContext, RunCheckpoint, TestHistoryItem } from "@coding-agent/types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

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
  return request<{ items: TreeEntry[]; truncated: boolean }>(`/api/v1/repositories/${repositoryId}/tree?max_depth=6`, { signal });
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

export function recoverTask(taskId: string): Promise<{ task: AgentTask; action: "recovered" }> {
  return request<{ task: AgentTask; action: "recovered" }>(`/api/v1/tasks/${taskId}/recover`, { method: "POST" });
}

export function createTaskBranch(taskId: string, name: string): Promise<GitSnapshot> {
  return request<GitSnapshot>(`/api/v1/tasks/${taskId}/branch`, {
    method: "POST",
    body: JSON.stringify({ name }),
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

export function createCommit(taskId: string, message: string, scopeHash: string): Promise<GitSnapshot> {
  return request<GitSnapshot>(`/api/v1/tasks/${taskId}/commits`, {
    method: "POST",
    body: JSON.stringify({ message, scope_hash: scopeHash }),
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

export function runTests(runId: string, kind: TestKind = "auto", target?: string): Promise<TestRunResult> {
  return request<TestRunResult>(`/api/v1/runs/${runId}/tests`, {
    method: "POST",
    body: JSON.stringify({ kind, target: target || undefined }),
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

export function getRepositoryContext(repositoryId: string, signal?: AbortSignal): Promise<RepositoryContext> {
  return request<RepositoryContext>(`/api/v1/repositories/${repositoryId}/context`, { signal });
}

export function readSourceFile(repositoryId: string, path: string, signal?: AbortSignal): Promise<SourceFile> {
  return request<SourceFile>(`/api/v1/repositories/${repositoryId}/files?path=${encodeURIComponent(path)}`, { signal });
}

export function searchRepository(repositoryId: string, query: string, glob?: string, signal?: AbortSignal): Promise<{ matches: SearchMatch[]; truncated: boolean }> {
  return request(`/api/v1/repositories/${repositoryId}/search`, { method: "POST", body: JSON.stringify({ query, glob: glob || undefined, max_results: 50 }), signal });
}

export function listCheckpoints(runId: string, signal?: AbortSignal): Promise<{ items: RunCheckpoint[]; total: number }> {
  return request(`/api/v1/runs/${runId}/checkpoints`, { signal });
}

export function listTestHistory(runId: string, signal?: AbortSignal): Promise<{ items: TestHistoryItem[]; total: number }> {
  return request(`/api/v1/runs/${runId}/tests`, { signal });
}

export function rollbackRun(runId: string, checkpointId: string): Promise<{ restored_files: string[]; restored_checkpoint_ids: string[] }> {
  return request(`/api/v1/runs/${runId}/rollback`, { method: "POST", body: JSON.stringify({ checkpoint_id: checkpointId }) });
}
