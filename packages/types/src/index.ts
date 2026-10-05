export type TaskStatus = "queued" | "running" | "review" | "done" | "failed";

export type WorkspaceSummary = {
  repositories: number;
  activeTasks: number;
  pendingApprovals: number;
  sandboxStatus: "ready" | "degraded";
};

export type AgentTask = {
  id: string;
  title: string;
  repository?: string;
  branch?: string;
  status: TaskStatus;
};

export type RepositorySummary = {
  id: string;
  name: string;
  path: string;
  branch: string;
  head: string | null;
  lastCommit: string | null;
  changedFiles: number;
  languages: Record<string, number>;
  packageManager: string | null;
};

export type SearchMatch = {
  path: string;
  line: number;
  column: number;
  text: string;
};
