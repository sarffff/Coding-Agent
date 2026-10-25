import { ApiRequestError } from "./api";
import type { Language } from "./preferences";

/**
 * The API speaks one language: English codes plus an English sentence. The UI is bilingual,
 * so every failure a person can see goes through this table first. Unknown codes fall back to
 * the server sentence rather than hiding the reason.
 */

let current: Language = "zh-CN";

export function setErrorLanguage(language: Language): void {
  current = language;
}

type Entry = { zh: string; en: string };

const MESSAGES: Record<string, Entry> = {
  API_ERROR: { zh: "请求失败。", en: "The request failed." },
  VALIDATION_ERROR: { zh: "请求参数不合法。", en: "The request parameters are invalid." },
  INTERNAL_ERROR: { zh: "服务端处理失败，请查看后端日志。", en: "The API could not complete the request; check the backend log." },

  MODEL_AUTH_FAILED: { zh: "模型 API Key 无效或未配置。请在「设置」里填写。", en: "The model API key is invalid or missing. Set it in Settings." },
  MODEL_TIMEOUT: { zh: "模型请求超时。可以调小上下文或稍后重试。", en: "The model request timed out. Retry, or send less context." },
  MODEL_NETWORK_ERROR: { zh: "连不上模型 API，检查网络或接口地址。", en: "Could not reach the model API; check the network or base URL." },
  MODEL_RATE_LIMITED: { zh: "模型接口限流，稍等再试。", en: "The model API is rate limiting; wait and retry." },
  MODEL_INVALID_OUTPUT: { zh: "模型返回的格式不对，已让它重试。", en: "The model returned malformed output; it has been asked to retry." },
  MODEL_NO_RESPONSE: { zh: "模型没有返回内容。", en: "The model returned nothing." },
  MODEL_ERROR: { zh: "模型接口报错。", en: "The model API returned an error." },
  MODEL_OUTPUT_TRUNCATED: { zh: "模型输出被长度上限截断，改用精确替换或减少文件数。", en: "The reply hit the token limit; use edits or fewer files." },
  MODEL_CONTEXT_LIMIT: { zh: "模型反复要上下文却没有改动，已停止。缩小目标或显式加入文件。", en: "The model kept asking for context without editing; narrow the goal or add files explicitly." },
  MODEL_CONTEXT_REQUIRED: { zh: "该文件还没完整读过，先加入上下文再改。", en: "That file has not been read in full; add it to context first." },
  MODEL_EDIT_CONFLICT: { zh: "精确替换没匹配上原文，文件可能在等待期间被改过。", en: "An edit did not match the file; it may have changed while waiting." },
  MODEL_NO_CHANGE: { zh: "模型提出的补丁没有实际变化，请检查失败原因或补充上下文。", en: "The proposal makes no changes; inspect the failure or add relevant context." },
  AGENT_NO_PROGRESS: { zh: "Agent 在相同上下文中重复无效建议，已停止。请审阅错误并调整任务或上下文。", en: "The agent repeated an invalid suggestion without new evidence and stopped. Review the error and revise the task or context." },
  AGENT_EXECUTION_FAILED: { zh: "Agent 执行失败并已停止，请查看错误记录后再继续。", en: "The agent stopped after an execution failure; review the error before continuing." },

  TASK_NOT_FOUND: { zh: "任务不存在。", en: "Task not found." },
  RUN_NOT_FOUND: { zh: "运行记录不存在。", en: "Run not found." },
  INVALID_TASK_STATE: { zh: "任务当前状态不允许这个操作。", en: "The task is not in a state that allows this." },
  TASK_BUDGET_EXHAUSTED: { zh: "任务预算已用尽，请先复核或取消。", en: "The task budget is exhausted; review or cancel it first." },
  AGENT_ACTION_NOT_AVAILABLE: { zh: "这一步已经变了，刷新后再试。", en: "That step already changed; refresh and retry." },
  LOOP_ALREADY_RUNNING: { zh: "这个任务已经有循环在跑。", en: "A loop is already running for this task." },
  COWORK_NO_LOOP: { zh: "Cowork 会话只聊天，不跑编码循环。", en: "Cowork conversations are chat-only; they do not run the coding loop." },
  WORKSPACE_BUSY: { zh: "还有任务在运行，先停止再操作。", en: "A task is still running; stop it first." },
  RECOVERY_CONFIRMATION_REQUIRED: { zh: "服务重启过，先确认继续。", en: "The API restarted; confirm recovery first." },
  TASK_REPOSITORY_REQUIRED: { zh: "先给任务关联一个文件夹。", en: "Connect a folder to this task first." },
  COMPLETION_REQUIRED: { zh: "请逐条填写完成证据并明确确认完成条件。", en: "Provide evidence for every completion criterion and explicitly confirm it." },
  COMPLETION_NOT_REQUIRED: { zh: "该任务没有需要确认的完成条件。", en: "This task has no completion criteria to confirm." },
  COMPLETION_CONFIRMATION_STALE: { zh: "目标范围、迭代或验证结果已改变，请重新复核完成条件。", en: "The scope, iteration or validation changed; review the completion criteria again." },
  UNCOMMITTED_TASK_CHANGES: { zh: "任务文件还有未提交的改动，请先审阅并提交后再发布。", en: "Task files still have uncommitted changes; review and commit them before publishing." },
  PUBLISH_NOT_READY: { zh: "当前迭代未通过验证或任务已停止，请修复并重新验证后再发布。", en: "The latest iteration has not passed validation or the task stopped; resolve it and validate before publishing." },
  PR_SCOPE_UNAVAILABLE: { zh: "本地缺少目标分支，无法核对完整 PR 范围。请获取目标分支后重新预览。", en: "The target branch is unavailable locally; fetch it and review the complete PR scope again." },
  COMPLETION_VALIDATION_REQUIRED: { zh: "先完成当前改动的全部必要验证，再确认完成条件。", en: "Pass all required validation for the current changes before confirming completion." },
  INVALID_COMPLETION_EVIDENCE: { zh: "每条完成条件都需要一条有效证据。", en: "Every completion criterion needs exactly one valid evidence entry." },

  REPOSITORY_NOT_FOUND: { zh: "文件夹未登记。", en: "Folder is not registered." },
  REPOSITORY_IN_USE: { zh: "该文件夹下还有任务，需要一并删除。", en: "This folder still has tasks; delete them with it." },
  PATH_NOT_FOUND: { zh: "路径不存在。", en: "Path not found." },
  PATH_NOT_DIRECTORY: { zh: "这个路径不是文件夹。", en: "That path is not a folder." },
  PATH_IS_FILESYSTEM_ROOT: { zh: "请选一个文件夹，不要选盘符或文件系统根。", en: "Choose a folder, not a drive or filesystem root." },
  PATH_OUTSIDE_REPOSITORY: { zh: "路径超出了所选文件夹。", en: "The path is outside the selected folder." },
  PROTECTED_PATH: { zh: "这个路径受保护，不能读写。", en: "That path is protected." },
  FILE_NOT_FOUND: { zh: "文件不存在。", en: "File not found." },
  FILE_NOT_READABLE: { zh: "文件读不了。", en: "The file could not be read." },
  FILE_TOO_LARGE: { zh: "文件超过大小上限。", en: "The file exceeds the size limit." },
  FILE_ENCODING_UNSUPPORTED: { zh: "文件编码不受支持。", en: "The file encoding is not supported." },
  BINARY_FILE_NOT_ALLOWED: { zh: "二进制文件不能这样修改。", en: "Binary files cannot be edited this way." },
  SYMLINK_NOT_ALLOWED: { zh: "不跟随符号链接。", en: "Symlinks are not followed." },

  PATCH_NOT_FOUND: { zh: "补丁不存在或已过期。", en: "Patch not found or expired." },
  PATCH_CONFLICT: { zh: "补丁与当前文件冲突。", en: "The patch conflicts with the current file." },
  PATCH_TOO_LARGE: { zh: "补丁超过大小上限。", en: "The patch exceeds the size limit." },
  PATCH_APPLY_FAILED: { zh: "写入失败，已回滚。", en: "Applying the patch failed and was rolled back." },
  PATCH_CONFIRMATION_REQUIRED: { zh: "补丁需要先预览确认。", en: "Preview the patch before applying it." },
  ROLLBACK_FAILED: { zh: "回滚失败。", en: "Rollback failed." },
  ROLLBACK_CONFLICT: { zh: "文件已被改动，无法回滚。", en: "The file changed; rollback is not safe." },
  CHECKPOINT_NOT_FOUND: { zh: "检查点不存在。", en: "Checkpoint not found." },

  GIT_NOT_AVAILABLE: { zh: "这个目录不是 Git 仓库。", en: "This folder is not a Git repository." },
  GIT_UNAVAILABLE: { zh: "服务端拉不起 git 进程，重启 API 再试。", en: "The API cannot start git; restart it." },
  GIT_COMMAND_FAILED: { zh: "Git 命令执行失败。", en: "A git command failed." },
  GIT_TIMEOUT: { zh: "Git 命令超时。", en: "The git command timed out." },
  GIT_BUSY: { zh: "有别的 Git 操作在进行。", en: "Another git operation is in progress." },
  WORKTREE_NOT_CLEAN: { zh: "工作区有未提交改动，先处理再继续。", en: "The worktree is not clean; commit or revert first." },
  NOTHING_TO_COMMIT: { zh: "没有可提交的改动。", en: "There is nothing to commit." },
  COMMIT_PREVIEW_STALE: { zh: "提交预览已过期，重新预览。", en: "The commit preview is stale; preview again." },
  TEST_RESULTS_STALE: { zh: "验证结果已过期，需要重跑。", en: "Validation results are stale; rerun them." },
  NO_APPROVED_COMMIT: { zh: "还没有通过审批的提交。", en: "There is no approved commit yet." },
  NO_TASK_BRANCH: { zh: "还没有任务分支。", en: "There is no task branch yet." },
  TASK_BRANCH_REQUIRED: { zh: "先创建任务分支。", en: "Create the task branch first." },
  BRANCH_NOT_PUSHED: { zh: "分支还没推送到远端。", en: "The branch has not been pushed." },
  PUSH_REJECTED: { zh: "远端拒绝了这次推送。", en: "The remote rejected the push." },
  PUSHED_COMMIT_UNRESOLVED: { zh: "推送成功但拿不到远端提交号。", en: "The push succeeded but the remote commit could not be resolved." },
  PR_CREATION_FAILED: { zh: "创建 PR 失败。", en: "Creating the pull request failed." },
  PR_APPROVAL_REQUIRED: { zh: "创建 PR 需要先审批。", en: "Creating a pull request needs approval." },
  PUSH_APPROVAL_REQUIRED: { zh: "推送需要先审批。", en: "Pushing needs approval." },
  REMOTE_NOT_FOUND: { zh: "仓库没有配置该远端。", en: "That remote is not configured." },
  REMOTE_TIMEOUT: { zh: "远端请求超时。", en: "The remote request timed out." },
  REMOTE_NETWORK_ERROR: { zh: "连不上远端。", en: "Could not reach the remote." },
  REMOTE_HEAD_MOVED: { zh: "远端分支已经变了，先同步。", en: "The remote branch moved; sync first." },
  AUTHENTICATION_FAILED: { zh: "远端认证失败，检查令牌。", en: "Remote authentication failed; check the token." },
  RATE_LIMITED_OR_FORBIDDEN: { zh: "远端限流或无权限。", en: "The remote rate limited the request or denied it." },

  APPROVAL_NOT_FOUND: { zh: "审批记录不存在。", en: "Approval not found." },
  APPROVAL_NOT_PENDING: { zh: "这条审批已经处理过了。", en: "That approval has already been decided." },
  APPROVAL_REASON_REQUIRED: { zh: "拒绝或要求修改必须写理由。", en: "Rejecting or requesting changes needs a reason." },
  APPROVAL_REQUIRED: { zh: "这一步需要人工审批。", en: "This step needs human approval." },
  APPROVAL_CONTINUATION_UNAVAILABLE: { zh: "这次要求修改不能继续，已有更新的审批或任务已结束。", en: "This request-changes cannot continue; a newer approval exists or the task ended." },

  INVALID_TEST_WORKDIR: { zh: "验证的工作目录不合法。", en: "The validation working directory is invalid." },
  INVALID_TEST_TARGET: { zh: "验证目标不合法。", en: "The validation target is invalid." },
  INVALID_TEST_SCRIPT: { zh: "脚本名必须是 package.json 里声明的。", en: "The script must be declared in package.json." },
  PROJECT_PYTHON_UNAVAILABLE: { zh: "项目的虚拟环境里找不到 Python。", en: "The project virtualenv has no Python interpreter." },
  VALIDATION_SCOPE_TOO_LARGE: { zh: "参与校验的文件太多，缩小范围再试。", en: "Too many files for validation; narrow the workspace." },
  VALIDATION_INPUT_UNAVAILABLE: { zh: "有校验输入读不了。", en: "A validation input could not be read." },
  VALIDATION_REVIEW_REQUIRED: { zh: "补丁改变了已有测试或验证脚本，请先审阅并明确确认验收变更。", en: "This patch changes existing tests or validation scripts; review and explicitly confirm the acceptance changes." },
  VALIDATION_BASELINE_BLOCKED: { zh: "验收基线已发生未确认的变化，请审阅受影响的测试或验证脚本后继续。", en: "Acceptance inputs changed without confirmation; review the affected tests or scripts before continuing." },
  TEST_ALREADY_RUNNING: { zh: "已有验证在跑。", en: "A validation is already running." },

  CONFIG_EMPTY: { zh: "没有要保存的改动。", en: "Nothing to save." },
  CONFIG_INVALID_VALUE: { zh: "这个值不合法。", en: "That value is invalid." },
};

type ValidationIssue = {
  loc?: Array<string | number>;
  type?: string;
  msg?: string;
  ctx?: Record<string, unknown>;
};

/** "goal needs at least 3 characters" is what the user needs; "请求参数不合法" alone is not. */
function describeIssue(issue: ValidationIssue): string {
  const zh = current !== "en-US";
  const ctx = issue.ctx ?? {};
  switch (issue.type) {
    case "string_too_short":
      return zh ? `至少 ${ctx.min_length} 个字符` : `needs at least ${ctx.min_length} characters`;
    case "string_too_long":
      return zh ? `最多 ${ctx.max_length} 个字符` : `allows at most ${ctx.max_length} characters`;
    case "missing":
      return zh ? "缺失" : "is required";
    case "literal_error":
      return zh ? "取值不在允许范围" : "is not an allowed value";
    case "greater_than_equal":
      return zh ? `不能小于 ${ctx.ge}` : `must be >= ${ctx.ge}`;
    case "less_than_equal":
      return zh ? `不能大于 ${ctx.le}` : `must be <= ${ctx.le}`;
    default:
      return issue.msg ?? (zh ? "不合法" : "is invalid");
  }
}

function describeValidationDetails(details: unknown): string {
  const errors = (details as { errors?: ValidationIssue[] } | undefined)?.errors;
  if (!Array.isArray(errors) || !errors.length) return "";
  return errors
    .slice(0, 2)
    .map((issue) => {
      const field = (issue.loc ?? []).filter((part) => part !== "body" && part !== "query").join(".");
      const reason = describeIssue(issue);
      return field ? `${field} ${reason}` : reason;
    })
    .join(current === "en-US" ? "; " : "；");
}

export function describeError(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    const entry = MESSAGES[error.code];
    const base = entry ? (current === "en-US" ? entry.en : entry.zh) : error.message || fallback;
    const issues = describeValidationDetails(error.details);
    return issues ? `${base.replace(/[。.]$/, "")}：${issues}` : base;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
