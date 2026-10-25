import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Bot, Check, ChevronDown, Code2, Copy, FileSearch, FolderOpen, FolderPlus, GitBranch, Monitor, Paperclip, Rocket, ShieldCheck, Sparkles, Square, Trash2, Workflow, X } from "lucide-react";
import type { AgentMode, Approval, AuditEvent, TaskBudget, TaskTrack } from "../lib/api";
import { usePreferences, type Language } from "../lib/preferences";
import { useChatScrollPinning } from "./useChatScrollPinning";

type ChatRole = "user" | "assistant" | "tool" | "system";

type ChatTurn = {
  id: string;
  role: ChatRole;
  title: string;
  body?: string;
  bodyPre?: boolean;
  steps?: string[];
  collapse?: boolean;
  thinking?: boolean;
  status?: "started" | "succeeded" | "failed";
  time: string;
};

type ChatItem = { kind: "turn"; turn: ChatTurn } | { kind: "tools"; id: string; turns: ChatTurn[] };

type ChatPlan = { objective: string; steps: Array<{ id: string; title: string }> } | undefined;

type ChatPanelProps = {
  reviewCompletion?: boolean;
  onOpenCompletion?: () => void;
  goal: string | null;
  plan: ChatPlan;
  events: AuditEvent[];
  loopRunning: boolean;
  sending: boolean;
  status: string | null;
  hasActiveRun: boolean;
  track: TaskTrack;
  repositories: Array<{ id: string; name: string; path: string; has_git?: boolean }>;
  selectedRepositoryId: string;
  onSelectRepository: (id: string) => void;
  onImportFolder: () => void;
  onBrowseFolder: () => void;
  onSend: (message: string) => Promise<boolean>;
  onStop: () => void | Promise<void>;
  onOpenPublish: () => void;
  reviewPatch: boolean;
  onReviewPatch: () => void;
  prepareTask?: boolean;
  onPrepareTask?: () => void;
  error: string | null;
  mode: AgentMode;
  modeLocked: boolean;
  onModeChange: (mode: AgentMode) => void;
  pending: Approval | null;
  onDecide: (approvalId: string, decision: "approve" | "reject", reason?: string) => void | Promise<void>;
  budget: TaskBudget | null;
  modelName?: string;
  onDelete?: () => void | Promise<void>;
};

const MODES: AgentMode[] = ["conservative", "standard", "autonomous"];

// Attachments ride along in the message text; keep them small enough that the prompt still fits.
const MAX_ATTACHMENTS = 5;
const MAX_ATTACHMENT_BYTES = 64 * 1024;

const IDEAS: Array<{ key: string; icon: typeof FileSearch }> = [
  { key: "chat.idea.organize", icon: Workflow },
  { key: "chat.idea.explain", icon: FileSearch },
  { key: "chat.idea.plan", icon: Sparkles },
];

function roleForEvent(action: string): ChatRole {
  const a = action.toLowerCase();
  if (a === "chat.user") return "user";
  if (a === "chat.assistant") return "assistant";
  if (a.startsWith("patch.draft") || a.startsWith("iteration.") || a === "agent.plan") return "assistant";
  if (a.startsWith("patch.") || a.startsWith("tests.") || a.startsWith("git.") || a.startsWith("checkpoint.") || a === "context.request") return "tool";
  return "system";
}

function Disclosure({ title, children, className = "", live = false }: { title: ReactNode; children: ReactNode; className?: string; live?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const open = live || expanded;
  return <div className={`chat-disclosure ${className}`} data-open={open}>
    <button type="button" className="chat-disclosure-trigger" aria-expanded={open} aria-controls={id} disabled={live} onClick={() => setExpanded(value => !value)}>
      {title}<ChevronDown size={14} aria-hidden="true" />
    </button>
    <div className="chat-disclosure-body" id={id} inert={!open}>
      <div className="chat-disclosure-content">{children}</div>
    </div>
  </div>;
}

function ChatMessage({ turn, live }: { turn: ChatTurn; live: boolean }) {
  const { t } = usePreferences();
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (copyState === "idle") return;
    const timer = window.setTimeout(() => setCopyState("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [copyState]);
  const copyMessage = async () => {
    const text = [turn.title, turn.steps?.map((step, index) => `${index + 1}. ${step}`).join("\n"), turn.body].filter(Boolean).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };
  return <article className={`chat-turn chat-turn--${turn.role}`} data-cds={turn.role === "user" ? "UserMessage" : "AssistantMessage"} data-state={turn.status}>
    {turn.role === "assistant" ? <div className="chat-message-meta"><Sparkles size={14} aria-hidden="true" /><span className="chat-message-author">Agent</span>{turn.time ? <time className="chat-time">{turn.time}</time> : null}</div> : null}
    {turn.role === "user" ? <div className="chat-bubble">{turn.title}</div> : turn.steps ? <>
      <div className="chat-msg-label">{turn.title}</div>
      <ol className="chat-plan">{turn.steps.map((step, index) => <li key={index}>{step}</li>)}</ol>
    </> : turn.collapse && turn.body ? <Disclosure className={turn.thinking ? "chat-thinking" : ""} live={live} title={<span>{turn.thinking ? <span className="chat-thinking-dot" aria-hidden="true" /> : null}{turn.title}</span>}>
      {turn.bodyPre ? <pre className="chat-bubble-body">{turn.body}</pre> : <div className="chat-prose">{turn.body}</div>}
    </Disclosure> : <div className="chat-msg">{turn.title}</div>}
    <div className="chat-message-actions" role="toolbar" aria-label={t("chat.copy")}>
      {turn.role === "user" && turn.time ? <time className="chat-time">{turn.time}</time> : null}
      <button type="button" className="chat-copy-message" onClick={() => void copyMessage()} title={t("chat.copy")} aria-label={t("chat.copy")}>{copyState === "copied" ? <Check size={14} /> : <Copy size={14} />}</button>
      {copyState !== "idle" ? <span role="status">{t(copyState === "copied" ? "chat.copied" : "chat.copyError")}</span> : null}
    </div>
  </article>;
}

export function ChatPanel(props: ChatPanelProps) {
  const { t, language } = usePreferences();
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Array<{ name: string; content: string }>>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [submitting, setSubmitting] = useState(false);
  const sendingRef = useRef(false);
  const composingRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const folderPickerRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    const closePicker = (event: PointerEvent) => {
      const picker = folderPickerRef.current;
      if (picker?.open && !picker.contains(event.target as Node)) picker.open = false;
    };
    document.addEventListener("pointerdown", closePicker);
    return () => document.removeEventListener("pointerdown", closePicker);
  }, []);

  const items = useMemo<ChatItem[]>(() => {
    const turns: ChatTurn[] = [];
    if (props.goal) turns.push({ id: "goal", role: "user", title: props.goal, time: "" });
    if (props.plan) turns.push({ id: "plan", role: "assistant", title: t("chat.plan"), steps: props.plan.steps.map((step) => step.title), time: "" });
    for (const event of props.events) {
      if (event.action === "task.create") continue; // the goal turn already shows this
      const role = roleForEvent(event.action);
      const rationale = typeof event.details?.rationale === "string" ? event.details.rationale : undefined;
      const failed = Array.isArray(event.details?.failed_tests) ? (event.details.failed_tests as Array<{ message?: string }>).map((item) => item.message).filter(Boolean).join("\n") : undefined;
      const output = typeof event.details?.output === "string" && event.details.output ? event.details.output : undefined;
      turns.push({
        id: event.id,
        role,
        title: event.action === "patch.draft" ? t("chat.thinking") : event.summary,
        body: rationale || failed || output || undefined,
        bodyPre: Boolean(failed || output),
        collapse: Boolean(rationale || failed || output),
        thinking: event.action.startsWith("patch.draft"),
        status: event.status,
        time: new Date(event.created_at).toLocaleTimeString(language as Language, { hour12: false }),
      });
    }
    // Coalesce consecutive tool/system turns into one "Used N tools" group, like Claude/ChatGPT.
    const list: ChatItem[] = [];
    let bucket: ChatTurn[] = [];
    const flush = () => {
      if (!bucket.length) return;
      if (bucket.length === 1) list.push({ kind: "turn", turn: bucket[0] });
      else list.push({ kind: "tools", id: `tools-${bucket[0].id}`, turns: bucket });
      bucket = [];
    };
    for (const turn of turns) {
      if (turn.role === "tool" || turn.role === "system") { bucket.push(turn); continue; }
      flush();
      list.push({ kind: "turn", turn });
    }
    flush();
    return list;
  }, [props.goal, props.plan, props.events, t, language]);

  const terminal = !props.hasActiveRun || ["done", "completed", "cancelled"].includes(props.status ?? "");
  const repository = props.repositories.find(item => item.id === props.selectedRepositoryId);
  const readyToPublish = repository?.has_git === true && (props.status === "ready_for_pr" || props.status === "pr_created");
  const localReady = repository?.has_git === false && props.status === "ready_for_pr";
  const busy = props.loopRunning || props.sending || submitting;
  const conversationKey = `${props.track}:${props.events[0]?.task_id ?? props.events[0]?.run_id ?? props.goal ?? ""}`;
  const { containerRef, contentRef, showJumpControl, markUserInput, updatePosition, scrollToBottom } = useChatScrollPinning(items, conversationKey);

  useEffect(() => {
    const input = inputRef.current;
    if (!input || CSS.supports("field-sizing", "content")) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 384)}px`;
  }, [draft]);

  const pickFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const room = MAX_ATTACHMENTS - attachments.length;
    if (room <= 0) {
      setAttachmentError(t("chat.attachmentLimit"));
      return;
    }
    const picked = Array.from(files).slice(0, room);
    const accepted: Array<{ name: string; content: string }> = [];
    let rejected: string | null = null;
    for (const file of picked) {
      if (file.size > MAX_ATTACHMENT_BYTES) {
        rejected = t("chat.attachmentTooLarge").replace("{name}", file.name).replace("{size}", String(Math.round(MAX_ATTACHMENT_BYTES / 1024)));
        continue;
      }
      accepted.push({ name: file.name, content: await file.text() });
    }
    setAttachmentError(rejected);
    if (accepted.length) setAttachments((current) => [...current, ...accepted]);
  };

  const withAttachments = (message: string) => attachments.length
    ? `${message}\n\n${attachments.map((file) => `# Attached: ${file.name}\n\`\`\`\n${file.content}\n\`\`\``).join("\n\n")}`
    : message;

  const send = async () => {
    const message = draft.trim();
    if ((!message && !attachments.length) || busy || sendingRef.current) return;
    sendingRef.current = true;
    setSubmitting(true);
    // Clear on send like every other chat: a slow reply must not leave the box looking unsent.
    setDraft("");
    try {
      if (await props.onSend(withAttachments(message || t("chat.attachmentOnly")))) {
        setAttachments([]);
        setAttachmentError(null);
        scrollToBottom();
        return;
      }
      // Rejected: hand the text back, but never overwrite something typed meanwhile.
      setDraft((current) => current || message);
    } catch {
      setDraft((current) => current || message);
    } finally {
      sendingRef.current = false;
      setSubmitting(false);
    }
  };

  const fillIdea = (text: string) => {
    setDraft(text);
    inputRef.current?.focus();
  };

  const pending = props.pending;
  const isStart = items.length === 0;
  const isCode = props.track === "code";
  const selectFolder = (id: string) => {
    props.onSelectRepository(id);
    if (folderPickerRef.current) {
      folderPickerRef.current.open = false;
      folderPickerRef.current.querySelector("summary")?.focus();
    }
  };
  const openFolder = () => {
    if (folderPickerRef.current) folderPickerRef.current.open = false;
    props.onImportFolder();
  };

  const modeSelector = (
    <label className={`chat-mode-select chat-mode-select--${props.mode}`} title={t(`chat.mode.${props.mode}.hint`)}>
      <span className="chat-mode-dot" aria-hidden="true" />
      <span className="sr-only">{t("chat.mode.label")}</span>
      <select value={props.mode} onChange={(event) => props.onModeChange(event.target.value as AgentMode)} disabled={busy || props.modeLocked}>
        {MODES.map((m) => <option key={m} value={m}>{t(`chat.mode.${m}`)}</option>)}
      </select>
      <ChevronDown size={12} aria-hidden="true" />
    </label>
  );
  const folderPicker = terminal ? (
    <details className="folder-picker" ref={folderPickerRef} onKeyDown={event => {
      if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
    }}>
      <summary className="folder-picker-trigger" aria-disabled={busy} aria-label={repository?.name ?? t("chat.chooseFolder")} title={repository?.name ?? t("chat.chooseFolder")} onClick={event => { if (busy) event.preventDefault(); }}>
        <FolderOpen size={16} />
        {isCode ? null : <span className="folder-picker-label"><span>{t("chat.projectFolder")}</span><strong>{repository?.name ?? t("chat.noFolder")}</strong></span>}
        <ChevronDown size={isCode ? 12 : 14} />
      </summary>
      <div className="folder-picker-menu" aria-label={t("chat.projectFolder")}>
        <div className="folder-picker-heading">{t("folder.recent")}</div>
        {!isCode ? <button type="button" className={`folder-picker-option ${!repository ? "is-selected" : ""}`} disabled={busy} aria-pressed={!repository} onClick={() => selectFolder("")}><X size={15} /><span className="folder-picker-option-copy"><strong>{t("chat.noFolder")}</strong><small>{t("chat.noFolderHint")}</small></span>{!repository ? <Check size={14} /> : null}</button> : null}
        {props.repositories.map(item => <button type="button" className={`folder-picker-option ${item.id === props.selectedRepositoryId ? "is-selected" : ""}`} key={item.id} disabled={busy} aria-pressed={item.id === props.selectedRepositoryId} onClick={() => selectFolder(item.id)}>
          <FolderOpen size={16} /><span className="folder-picker-option-copy"><strong>{item.name}</strong><small title={item.path}>{item.path}</small></span>{item.id === props.selectedRepositoryId ? <Check size={14} /> : null}
        </button>)}
        {!props.repositories.length ? <p className="folder-picker-empty">{t("folder.emptyRecent")}</p> : null}
        <button type="button" className="folder-picker-open" onClick={openFolder} disabled={busy}><FolderPlus size={16} /><span>{t("folder.openLocal")}</span></button>
      </div>
    </details>
  ) : <span className="chat-folder-current" title={repository?.path}><FolderOpen size={15} />{isCode ? null : (repository?.name ?? t("chat.noFolder"))}</span>;
  // Code mode keeps the composer to model + permission + send: the chosen folder is a detail,
  // not something that needs a row of its own under the textarea.
  const composerMeta = (
    <div className={`chat-composer-meta${isCode ? " chat-composer-meta--compact" : ""}`}>
      {folderPicker}
      {!isCode && repository ? <span className={`chat-folder-chip chat-folder-chip--${repository.has_git ? "git" : "local"}`} title={t(repository.has_git ? "chat.gitFolderHint" : "chat.localFolderHint")}>{repository.has_git ? <GitBranch size={13} /> : <Monitor size={13} />}{repository.has_git ? "Git" : "Local"}</span> : null}
      {!isCode && repository ? <button type="button" className="chat-folder-browse" onClick={props.onBrowseFolder} title={repository.path}><FileSearch size={14} /><span>{t("chat.browseFolder")}</span></button> : null}
    </div>
  );
  const composerCard = (
    <div className="chat-composer-card" data-cds="ChatComposer">
      {attachments.length ? <div className="chat-attachments">{attachments.map((file, index) => <span className="chat-attachment" key={file.name + index}><Paperclip size={12} /><span title={file.name}>{file.name}</span><button type="button" aria-label={t("chat.removeAttachment")} onClick={() => setAttachments(current => current.filter((_, i) => i !== index))} disabled={busy}><X size={12} /></button></span>)}</div> : null}
      <textarea ref={inputRef} value={draft} onChange={event => setDraft(event.target.value)} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }} onKeyDown={event => {
        if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); }
      }} placeholder={t(isCode ? "chat.placeholder" : "chat.coworkPlaceholder")} rows={1} disabled={busy} aria-label={t("chat.subtitle")} />
      <div className="chat-composer-bar">
        <div className="chat-composer-controls">{composerMeta}{isCode ? modeSelector : null}</div>
        <div className="chat-composer-actions">
          <input ref={fileRef} type="file" multiple hidden accept=".txt,.md,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.ini,.cfg,.sql,.sh,.css,.html,.java,.go,.rs,.rb,.php,.c,.h,.cpp" onChange={(event) => { void pickFiles(event.target.files); event.target.value = ""; }} />
          <button type="button" className="chat-attach" onClick={() => fileRef.current?.click()} disabled={busy || attachments.length >= MAX_ATTACHMENTS} aria-label={t("chat.attach")} title={t("chat.attach")}><Paperclip size={16} /></button>
          {props.modelName ? <span className="chat-model-chip" title={t("chat.modelChip")}>{props.modelName}</span> : null}
          {props.loopRunning ? <button type="button" className="chat-send chat-send--stop" onClick={() => void props.onStop()} disabled={props.sending} aria-label={t("chat.stop")} title={t("chat.stop")}><Square size={14} fill="currentColor" /></button> : <button type="button" className="chat-send" onClick={() => void send()} disabled={(!draft.trim() && !attachments.length) || busy} aria-label={t("chat.send")} title={t("chat.send")}><ArrowUp size={18} /></button>}
        </div>
      </div>
      {attachmentError ? <p className="chat-attachment-error" role="alert">{attachmentError}</p> : null}
    </div>
  );
  const composer = (
    <div className={`chat-composer chat-composer--${props.track}${isStart && !isCode ? " chat-composer--start" : ""}`}>
      {composerCard}
      <div className="chat-composer-footer"><span className="chat-composer-hint">{t("chat.hint")}</span></div>
    </div>
  );

  const renderActivity = (turn: ChatTurn) => (
    <div className="chat-activity" key={turn.id} data-state={turn.status}>
      <div className="chat-activity-row">
        {turn.status === "succeeded" ? <Check size={13} className="chat-head-icon" /> : turn.status === "failed" ? <X size={13} className="chat-head-icon" /> : <Workflow size={13} className="chat-head-icon" />}
        <span className="chat-activity-text">{turn.title}</span>
        {turn.time ? <time className="chat-time">{turn.time}</time> : null}
      </div>
      {turn.body ? <Disclosure title={<span>{t("chat.details")}</span>}><pre className="chat-bubble-body">{turn.body}</pre></Disclosure> : null}
    </div>
  );

  const renderTurn = (turn: ChatTurn) => {
    if (turn.role === "tool" || turn.role === "system") return renderActivity(turn);
    return <ChatMessage key={turn.id} turn={turn} live={busy && turn.status === "started" && turn.id === props.events.at(-1)?.id} />;
  };

  return (
    <section className={`chat-view chat-view--${props.track}${isStart ? " chat-view--empty" : ""}`}>
      <div className="chat-main">
        {props.error ? <div className="inline-error" role="alert">{props.error}</div> : null}
        {isStart && isCode ? <>
          <div className="chat-code-welcome">
            <Code2 size={24} className="chat-empty-mark" />
            <h2>{t("chat.codeGreeting")}</h2>
            <p>{t(repository ? "chat.codeFolderReady" : "chat.codeChooseFolder")}</p>
            {repository ? <div className="chat-code-folder"><FolderOpen size={16} /><div><strong>{repository.name}</strong><span title={repository.path}>{repository.path}</span></div></div> : <button type="button" className="chat-open-folder" onClick={openFolder} disabled={busy}><FolderPlus size={16} />{t("folder.openLocal")}</button>}
          </div>
          {composer}
        </> : isStart ? (
          <div className="chat-start">
            <div className="chat-start-head">
              <Sparkles size={30} className="chat-empty-mark" />
              <h2 className="chat-empty-title">{t("chat.coworkGreeting")}</h2>
            </div>
            {composer}
            <div className="chat-ideas">
              <div className="chat-ideas-label">{t("chat.ideas")}</div>
              {IDEAS.map(({ key, icon: Icon }) => (
                <button key={key} className="chat-idea" disabled={busy} onClick={() => fillIdea(t(key))}>
                  <Icon size={16} className="chat-idea-icon" /><span>{t(key)}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="chat-header">
        <div><div className="section-kicker"><Bot size={13} /> {t("chat.title")}</div><h2>{t("chat.subtitle")}</h2></div>
        <div className="chat-header-actions">{props.onDelete && !props.loopRunning ? <button className="small-action small-action--muted" onClick={() => void props.onDelete?.()} disabled={busy}><Trash2 size={13} /> {t("chat.delete")}</button> : null}{props.loopRunning ? <button className="small-action small-action--muted" onClick={() => void props.onStop()}><Square size={13} /> {t("chat.stop")}</button> : <span className="chat-state">{terminal ? t("chat.idle") : t("chat.active")}</span>}</div>
      </div>

      {props.budget ? (
        <div className="chat-usage">
          <span>{t("chat.usage.iterations")} {props.budget.iterations}/{props.budget.max_iterations}</span>
          <span>{t("chat.usage.calls")} {props.budget.remote_calls}/{props.budget.max_remote_calls}</span>
          {props.budget.exhausted_reason ? <span className="chat-usage-exhausted">{t("chat.usage.exhausted")}</span> : null}
        </div>
      ) : null}

            <div className="chat-transcript-shell">
              <div className="chat-transcript" ref={containerRef} onScroll={updatePosition} onWheel={markUserInput} onTouchMove={markUserInput} onPointerDown={event => { if (event.target === event.currentTarget) markUserInput(); }} tabIndex={0} aria-label={t("chat.title")} onKeyDown={event => {
                if (event.target === event.currentTarget && ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) markUserInput();
              }}>
                <div className="chat-transcript-content" ref={contentRef}>
                  {items.map(item => item.kind === "tools" ? <Disclosure className="chat-tools" key={item.id} title={<span><Workflow size={13} /> {t("chat.usedTools").replace("{n}", String(item.turns.length))}</span>}>
                    <div className="chat-tools-list">{item.turns.map(renderActivity)}</div>
                  </Disclosure> : renderTurn(item.turn))}
                  {busy ? <div className="chat-working" role="status"><span className="timeline-quiet-dots" aria-hidden="true"><span /><span /><span /></span>{t("chat.working")}</div> : null}
                </div>
              </div>
              {showJumpControl ? <button type="button" className="chat-jump-latest" onClick={scrollToBottom} aria-label={t("chat.jumpLatest")} title={t("chat.jumpLatest")}><ArrowDown size={18} /></button> : null}
            </div>

      {pending ? (
        <div className={`chat-approval chat-approval--${pending.type}`} data-cds="ToolDecisionCard" data-state={busy ? "running" : "pending_approval"} role="region" aria-label={t(`chat.approval.${pending.type}`)}>
          <div className="chat-approval-head"><ShieldCheck size={14} /><span>{t(`chat.approval.${pending.type}`)}</span><span className="chat-approval-status"><span className="chat-thinking-dot" aria-hidden="true" />{t(busy ? "working" : "awaitingApproval")}</span></div>
          <div className="chat-approval-summary">{pending.summary}</div>
          <div className="chat-approval-actions">
            <button type="button" className="chat-reject" disabled={busy} onClick={() => void props.onDecide(pending.id, "reject", t("chat.rejectReason"))}><X size={14} /> {t("chat.reject")}</button>
            <button type="button" className="chat-approve" disabled={busy} onClick={() => void props.onDecide(pending.id, "approve")}><Check size={14} /> {t("chat.approve")}</button>
          </div>
        </div>
      ) : null}

      {props.reviewCompletion ? <div className="chat-handoff"><ShieldCheck size={14} /><span>{t("completion.reviewHint")}</span><button className="small-action" onClick={props.onOpenCompletion}>{t("completion.review")}</button></div> : null}
      {readyToPublish && !props.reviewCompletion ? <div className="chat-handoff"><Rocket size={14} /><span>{t("chat.handoff")}</span><button className="small-action" onClick={props.onOpenPublish}>{t("chat.openPublish")}</button></div> : null}
      {localReady && !props.reviewCompletion ? <div className="chat-handoff"><FileSearch size={14} /><span>{t("chat.localComplete")}</span><button className="small-action" onClick={props.onReviewPatch}>{t("chat.reviewLocal")}</button></div> : null}
      {props.prepareTask ? <div className="chat-handoff"><ShieldCheck size={14} /><span>{t("chat.setupHint")}</span><button className="small-action" onClick={props.onPrepareTask} disabled={busy}>{t("chat.setupTitle")}</button></div> : null}
      {props.reviewPatch ? <div className="chat-handoff"><FileSearch size={14} /><span>{t("chat.reviewPatchHint")}</span><button className="small-action" onClick={props.onReviewPatch}>{t("chat.reviewPatch")}</button></div> : null}

            {composer}
          </>
        )}
      </div>
    </section>
  );
}
