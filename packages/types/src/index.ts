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
  branch: string | null;
  task_branch: string | null;
  resume_state: AgentTask["status"] | null;
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
  test_runs: TestRunResult[];
  rolled_back: boolean;
  failure_summary: string | null;
  created_at: string;
  updated_at: string;
};

export type TestKind = "auto" | "pytest" | "frontend" | "static";

export type TestRunResult = {
  id: string;
  created_at: string;
  validation_hash: string | null;
  run_id: string;
  kind: TestKind;
  command: string[];
  status: "passed" | "failed" | "timed_out" | "not_found" | "blocked" | "cancelled";
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

export type CommitPreview = Omit<GitSnapshot, "clean"> & {
  diff: string;
  commit_message: string;
  scope_hash: string;
  excluded_files: string[];
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

export type SourceFile = {
  repository_id: string;
  path: string;
  content: string;
  content_hash: string;
  language: string | null;
  size: number;
  line_count: number;
  symbols: Array<{ name: string; kind: string; line: number }>;
};

export type RepositoryContext = {
  repository_id: string;
  languages: Record<string, number>;
  package_manager: string | null;
  entry_files: string[];
  test_directories: string[];
  config_files: string[];
  file_count: number;
  truncated: boolean;
};

export type RunCheckpoint = {
  id: string;
  repository_id: string;
  run_id: string;
  files: string[];
  created_at: string;
  restored: boolean;
};

export type TestHistoryItem = { iteration_id: string; iteration_number: number; result: TestRunResult };


export type TaskStatus = AgentTask["status"];
export type RepositorySummary = Repository;
