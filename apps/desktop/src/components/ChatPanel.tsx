import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Activity, AlertCircle, ArrowDown, ArrowUp, Check, ChevronDown, Code2, Copy, FileSearch, FolderOpen, FolderPlus, LockKeyhole, Paperclip, Rocket, Settings2, ShieldCheck, Sparkles, Square, Trash2, Workflow, X } from "lucide-react";
import type { AgentAction, AgentMode, AgentState, Approval, AuditEvent, CompletionCriterion, ModelProfileView, TaskBudget, TaskTrack } from "../lib/api";
import { usePreferences, type Language } from "../lib/preferences";
import { useChatScrollPinning } from "./useChatScrollPinning";
import { ChatMarkdown } from "./ChatMarkdown";
import { actionLabels, BudgetMeter } from "./AgentPanel";
import { ChatRunSummary } from "./ChatRunSummary";
import { ChatEventDetails } from "./ChatEventDetails";
import { ChatAttachments, readChatAttachments } from "./ChatAttachments";
import { loadChatDraft, moveChatDraft, saveChatDraft, type ChatAttachment } from "../lib/chatDrafts";
import "../styles/chat.css";

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
  event?: AuditEvent;
  attachments?: ChatAttachment[];
  stateNote?: string;
};

type ChatItem = { kind: "turn"; turn: ChatTurn } | { kind: "tools"; id: string; turns: ChatTurn[] };

type ChatPlan = { objective: string; steps: Array<{ id: string; title: string }> } | undefined;

export type ChatStreamingReply = { requestId: string; content: string; status: "streaming" | "failed" | "cancelled" | "completed"; message?: string; userMessage?: string; attachments?: ChatAttachment[] };

type ChatPanelProps = {
  sessionKey: string;
  draftScope?: string;
  draftSessionKey?: string;
  draftTaskKeys?: Record<string, string>;
  deletedDraftKeys?: string[];
  streamingReplies?: ChatStreamingReply[];
  reviewCompletion?: boolean;
  onOpenCompletion?: () => void;
  goal: string | null;
  plan: ChatPlan;
  events: AuditEvent[];
  loopRunning: boolean;
  sending: boolean;
  stateLoaded?: boolean;
  status: string | null;
  hasActiveRun: boolean;
  track: TaskTrack;
  repositories: Array<{ id: string; name: string; path: string; has_git?: boolean }>;
  selectedRepositoryId: string;
  onSelectRepository: (id: string) => void;
  onImportFolder: () => void;
  onBrowseFolder: () => void;
  canSetCompletionCriteria?: boolean;
  onSend: (message: string, completionCriteria?: CompletionCriterion[], attachments?: ChatAttachment[], messageText?: string) => Promise<boolean>;
  onStop: () => void | boolean | Promise<void | boolean>;
  onCancelCowork?: () => void | boolean | Promise<void | boolean>;
  stateMessage?: string | null;
  action?: AgentAction | null;
  agentState?: AgentState | null;
  requiresRecovery?: boolean;
  blockedReason?: string | null;
  onResume?: () => void | Promise<void>;
  onRecover?: () => void | Promise<void>;
  onOpenRun?: () => void;
  onPrepare?: () => void;
  onOpenContextPath?: (path: string) => void;
  onOpenFile?: (path: string, line?: number) => void;
  onOpenValidation?: () => void;
  onRefresh?: () => void;
  onOpenPublish: () => void;
  reviewPatch: boolean;
  onReviewPatch: () => void;
  error: string | null;
  mode: AgentMode;
  modeLocked: boolean;
  onModeChange: (mode: AgentMode) => void;
  pending: Approval | null;
  onDecide: (approvalId: string, decision: "approve" | "reject", reason?: string) => void | Promise<void>;
  budget: TaskBudget | null;
  modelName?: string;
  models?: ModelProfileView[];
  activeModelId?: string;
  modelLocked?: boolean;
  onModelChange?: (id: string) => void;
  onOpenModelSettings?: () => void;
  contextLimit?: number;
  onDelete?: () => void | Promise<void>;
};

const MODES: AgentMode[] = ["conservative", "standard", "autonomous"];

// Attachments ride along in the message text; keep them small enough that the prompt still fits.
const MAX_ATTACHMENTS = 5;
const MAX_ATTACHMENT_BYTES = 64 * 1024;
// Matches the API: a pasted file fits, an accidental log dump does not.
const MAX_MESSAGE_CHARS = 32_000;

type ComposerState = {
  draft: string;
  completionDraft: string;
  attachments: Array<{ name: string; content: string }>;
  attachmentError: string | null;
  readingAttachments: boolean;
  submitting: boolean;
};
const EMPTY_COMPOSER: ComposerState = { draft: "", completionDraft: "", attachments: [], attachmentError: null, readingAttachments: false, submitting: false };

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

function closeComposerMenu(button: HTMLElement) {
  const menu = button.closest("details");
  menu?.removeAttribute("open");
  menu?.querySelector("summary")?.focus();
}

function ComposerMenu({ label, trigger, className = "", triggerClass = "", disabled = false, children }: { label: string; trigger: ReactNode; className?: string; triggerClass?: string; disabled?: boolean; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const id = useId();
  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (ref.current?.open && !ref.current.contains(event.target as Node)) ref.current.open = false;
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  useEffect(() => { if (disabled && ref.current) ref.current.open = false; }, [disabled]);
  return <details className={`chat-popover ${className}`} ref={ref} onToggle={event => {
    if (!event.currentTarget.open) return;
    document.querySelectorAll<HTMLDetailsElement>(".chat-popover[open]").forEach(menu => {
      if (menu !== event.currentTarget) menu.open = false;
    });
  }} onKeyDown={event => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeComposerMenu(event.currentTarget.querySelector("summary")!);
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) || disabled) return;
    event.preventDefault();
    event.currentTarget.open = true;
    const options = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
    const index = options.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : event.key === "ArrowUp" ? (index <= 0 ? options.length - 1 : index - 1) : (index + 1) % options.length;
    options[next]?.focus();
  }}>
    <summary className={triggerClass} aria-label={label} title={label} aria-controls={id} aria-disabled={disabled} onClick={event => { if (disabled) event.preventDefault(); }}>{trigger}</summary>
    <div className="chat-popover-panel" id={id} role="region" aria-label={label}>{children}</div>
  </details>;
}

function ChatMessage({ turn, live, onOpenFile }: { turn: ChatTurn; live: boolean; onOpenFile?: ChatPanelProps["onOpenFile"] }) {
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
      {turn.bodyPre ? <pre className="chat-bubble-body">{turn.body}</pre> : <ChatMarkdown text={turn.body} />}
    </Disclosure> : turn.role === "assistant" ? <ChatMarkdown text={turn.title} /> : <div className="chat-msg">{turn.title}</div>}
    {turn.event ? <ChatEventDetails event={turn.event} onOpenFile={onOpenFile} /> : null}
    {turn.attachments?.length ? <ChatAttachments attachments={turn.attachments} /> : null}
    {turn.stateNote ? <p className={`chat-stream-state${turn.status === "failed" ? " is-incomplete" : ""}`} role="status">{turn.stateNote}</p> : null}
    <div className="chat-message-actions" role="toolbar" aria-label={t("chat.copy")}>
      {turn.role === "user" && turn.time ? <time className="chat-time">{turn.time}</time> : null}
      <button type="button" className="chat-copy-message" onClick={() => void copyMessage()} title={t("chat.copy")} aria-label={t("chat.copy")}>{copyState === "copied" ? <Check size={14} /> : <Copy size={14} />}</button>
      {copyState !== "idle" ? <span role="status">{t(copyState === "copied" ? "chat.copied" : "chat.copyError")}</span> : null}
    </div>
  </article>;
}

export function ChatPanel(props: ChatPanelProps) {
  const { t, language } = usePreferences();
  const sessionKey = props.sessionKey;
  const draftScope = props.draftScope ?? "local";
  const draftSessionKey = props.draftSessionKey ?? sessionKey;
  const composerKey = props.draftScope ? `${draftScope}:${sessionKey}` : sessionKey;
  const sessionRef = useRef(sessionKey);
  sessionRef.current = sessionKey;
  const [composers, setComposers] = useState<Record<string, ComposerState>>({});
  const [draftStorageErrors, setDraftStorageErrors] = useState<Record<string, boolean>>({});
  const draftLocationsRef = useRef(new Map<string, { scope: string; key: string }>());
  const persistedComposersRef = useRef(new Map<string, ComposerState>());
  const restoredDraft = useMemo(() => loadChatDraft(draftScope, draftSessionKey), [draftScope, draftSessionKey]);
  const [stoppingSessions, setStoppingSessions] = useState(new Set<string>());
  const currentComposer = composers[composerKey] ?? { ...EMPTY_COMPOSER, ...restoredDraft };
  const { draft, completionDraft, attachments, attachmentError, readingAttachments, submitting } = currentComposer;
  // Each render's setters retain their source session, including after an awaited send or file read.
  const updateComposer = (update: (current: ComposerState) => ComposerState) => setComposers(current => ({ ...current, [composerKey]: update(current[composerKey] ?? { ...EMPTY_COMPOSER, ...restoredDraft }) }));
  const setDraft = (value: string | ((current: string) => string)) => updateComposer(current => ({ ...current, draft: typeof value === "function" ? value(current.draft) : value }));
  const setCompletionDraft = (value: string) => updateComposer(current => ({ ...current, completionDraft: value }));
  const setAttachments = (value: ComposerState["attachments"] | ((current: ComposerState["attachments"]) => ComposerState["attachments"])) => updateComposer(current => ({ ...current, attachments: typeof value === "function" ? value(current.attachments) : value }));
  const setAttachmentError = (value: string | null) => updateComposer(current => ({ ...current, attachmentError: value }));
  const setReadingAttachments = (value: boolean) => updateComposer(current => ({ ...current, readingAttachments: value }));
  const setSubmitting = (value: boolean) => updateComposer(current => ({ ...current, submitting: value }));
  useEffect(() => {
    const deleted = new Set(props.deletedDraftKeys ?? []);
    const removed = new Set<string>();
    for (const [key, location] of draftLocationsRef.current) {
      if (location.scope !== draftScope || !deleted.has(location.key)) continue;
      removed.add(key);
      draftLocationsRef.current.delete(key);
      persistedComposersRef.current.delete(key);
    }
    for (const deletedKey of deleted) {
      const key = props.draftScope ? `${draftScope}:${deletedKey}` : deletedKey;
      if (!composers[key]) continue;
      removed.add(key);
      draftLocationsRef.current.delete(key);
      persistedComposersRef.current.delete(key);
    }
    if (removed.size) {
      setComposers(current => Object.fromEntries(Object.entries(current).filter(([key]) => !removed.has(key))));
      setDraftStorageErrors(current => Object.fromEntries(Object.entries(current).filter(([key]) => !removed.has(key))));
    }
    const migrate = (key: string, targetKey: string) => {
      const previous = draftLocationsRef.current.get(key);
      const current = composers[key];
      if (!previous || !current || deleted.has(targetKey)) return false;
      if (previous.scope === draftScope && previous.key === targetKey) return true;
      const pending = current.submitting ? loadChatDraft(previous.scope, previous.key) ?? current : current;
      const saved = previous.scope === draftScope && moveChatDraft(draftScope, previous.key, targetKey, pending);
      setDraftStorageErrors(errors => ({ ...errors, [key]: !saved }));
      if (saved) draftLocationsRef.current.set(key, { scope: draftScope, key: targetKey });
      return saved;
    };
    for (const [session, taskId] of Object.entries(props.draftTaskKeys ?? {})) {
      migrate(props.draftScope ? `${draftScope}:${session}` : session, taskId);
    }
    if (deleted.has(draftSessionKey) || removed.has(composerKey)) return;
    if (draftLocationsRef.current.has(composerKey) && !migrate(composerKey, draftSessionKey)) return;
    draftLocationsRef.current.set(composerKey, { scope: draftScope, key: draftSessionKey });
    setComposers(current => current[composerKey] ? current : { ...current, [composerKey]: { ...EMPTY_COMPOSER, ...restoredDraft } });
  }, [composerKey, draftScope, draftSessionKey, restoredDraft, composers, props.draftTaskKeys, props.deletedDraftKeys]);
  useEffect(() => {
    const errors: Record<string, boolean> = {};
    for (const [key, current] of Object.entries(composers)) {
      const location = draftLocationsRef.current.get(key);
      if (!location || current.submitting || persistedComposersRef.current.get(key) === current) continue;
      const saved = saveChatDraft(location.scope, location.key, current);
      errors[key] = !saved;
      if (saved) persistedComposersRef.current.set(key, current);
    }
    setDraftStorageErrors(current => Object.entries(errors).every(([key, value]) => current[key] === value) ? current : { ...current, ...errors });
  }, [composers]);
  const readingAttachmentsRef = useRef(new Set<string>());
  const sendingRef = useRef(new Set<string>());
  const fileRef = useRef<HTMLInputElement>(null);
  const composingRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const folderPickerRef = useRef<HTMLDetailsElement>(null);
  const stoppingRef = useRef(new Set<string>());
  const stopPendingRef = useRef(new Set<string>());
  const inputHintId = useId();
  const inputErrorId = useId();
  const clearStopping = (key: string) => {
    stoppingRef.current.delete(key);
    setStoppingSessions(current => { const next = new Set(current); next.delete(key); return next; });
  };
  useEffect(() => {
    if (stoppingRef.current.has(sessionKey) && !stopPendingRef.current.has(sessionKey) && props.stateLoaded !== false && !props.loopRunning && !props.sending) clearStopping(sessionKey);
  }, [sessionKey, props.loopRunning, props.sending, props.stateLoaded, stoppingSessions]);

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
    const created = props.events.find(event => event.action === "task.create");
    if (props.goal) turns.push({ id: "goal", role: "user", title: typeof created?.details.message_text === "string" ? created.details.message_text : props.goal, time: "", event: created, attachments: readChatAttachments(created?.details.attachments) });
    if (props.plan) turns.push({ id: "plan", role: "assistant", title: t("chat.plan"), steps: props.plan.steps.map((step) => step.title), time: "" });
    for (const event of props.events) {
      if (event.action === "task.create") continue; // the goal turn already shows this
      const role = roleForEvent(event.action);
      const rationale = typeof event.details?.rationale === "string" ? event.details.rationale : undefined;
      const output = typeof event.details?.output === "string" && event.details.output ? event.details.output : undefined;
      turns.push({
        id: event.id,
        role,
        title: role === "user" && typeof event.details.message_text === "string" ? event.details.message_text : event.action === "patch.draft" ? t("chat.thinking") : event.summary,
        body: rationale || output || undefined,
        bodyPre: Boolean(output),
        collapse: Boolean(rationale || output),
        thinking: event.action.startsWith("patch.draft"),
        status: event.status,
        time: new Date(event.created_at).toLocaleTimeString(language as Language, { hour12: false }),
        event,
        attachments: role === "user" ? readChatAttachments(event.details.attachments) : undefined,
      });
    }
    for (const stream of props.track === "cowork" ? props.streamingReplies ?? [] : []) {
      const recordedUser = props.events.some(event => event.action === "chat.user" && event.details.request_id === stream.requestId);
      const recordedReply = props.events.some(event => event.action === "chat.assistant" && event.details.request_id === stream.requestId);
      if (!recordedUser && stream.userMessage) turns.push({ id: `stream-user:${stream.requestId}`, role: "user", title: stream.userMessage, time: "", attachments: stream.attachments });
      if (!recordedReply && (stream.content || stream.status !== "streaming")) {
        const stateNote = stream.status === "streaming" ? (language === "zh-CN" ? "正在回复…" : "Replying…")
          : stream.status === "completed" ? (language === "zh-CN" ? "回复已收到，聊天记录尚待刷新。" : "Reply received; chat history is awaiting refresh.")
          : stream.status === "cancelled" ? (language === "zh-CN" ? "已停止，以上内容不完整。" : "Stopped; the reply above is incomplete.")
          : (language === "zh-CN" ? "回复中断，以上内容不完整。" : "Reply interrupted; the content above is incomplete.");
        turns.push({ id: `stream-reply:${stream.requestId}`, role: "assistant", title: stream.content || (language === "zh-CN" ? "未收到回复内容。" : "No reply content received."), time: "", status: stream.status === "streaming" ? "started" : stream.status === "completed" ? "succeeded" : "failed", stateNote: `${stateNote}${stream.message ? ` ${stream.message}` : ""}` });
      }
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
  }, [props.goal, props.plan, props.events, props.streamingReplies, props.track, t, language]);

  const terminal = !props.hasActiveRun || ["done", "completed", "cancelled"].includes(props.status ?? "");
  const repository = props.repositories.find(item => item.id === props.selectedRepositoryId);
  const readyToPublish = repository?.has_git === true && (props.status === "ready_for_pr" || props.status === "pr_created");
  const localReady = repository?.has_git === false && props.status === "ready_for_pr";
  const stopping = stoppingSessions.has(sessionKey);
  const busy = props.loopRunning || props.sending || submitting || readingAttachments || stopping;
  const conversationKey = sessionKey;
  const { containerRef, contentRef, showJumpControl, markUserInput, updatePosition, scrollToBottom } = useChatScrollPinning(items, conversationKey);

  useEffect(() => {
    const input = inputRef.current;
    if (!input || CSS.supports("field-sizing", "content")) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 384)}px`;
  }, [draft]);

  const pickFiles = async (files: FileList | null) => {
    if (!files?.length || busy || readingAttachmentsRef.current.has(sessionKey) || sendingRef.current.has(sessionKey)) return;
    const room = MAX_ATTACHMENTS - attachments.length;
    if (room <= 0) {
      setAttachmentError(t("chat.attachmentLimit"));
      return;
    }
    const picked = Array.from(files).slice(0, room);
    const accepted: Array<{ name: string; content: string }> = [];
    let rejected: string | null = files.length > room ? t("chat.attachmentLimit") : null;
    readingAttachmentsRef.current.add(sessionKey);
    setReadingAttachments(true);
    try {
      for (const file of picked) {
        if (file.size > MAX_ATTACHMENT_BYTES) {
          rejected = t("chat.attachmentTooLarge").replace("{name}", file.name).replace("{size}", String(Math.round(MAX_ATTACHMENT_BYTES / 1024)));
          continue;
        }
        accepted.push({ name: file.name, content: await file.text() });
      }
      setAttachmentError(rejected);
      if (accepted.length) setAttachments((current) => [...current, ...accepted].slice(0, MAX_ATTACHMENTS));
    } catch {
      setAttachmentError(t("chat.attachmentReadError"));
    } finally {
      readingAttachmentsRef.current.delete(sessionKey);
      setReadingAttachments(false);
    }
  };

  const withAttachments = (message: string) => attachments.length
    ? `${message}\n\n${attachments.map((file) => `# Attached: ${file.name}\n\`\`\`\n${file.content}\n\`\`\``).join("\n\n")}`
    : message;

  const send = async () => {
    const message = draft.trim();
    if ((!message && !attachments.length) || busy || sendingRef.current.has(sessionKey) || readingAttachmentsRef.current.has(sessionKey)) return;
    const payload = withAttachments(message || t("chat.attachmentOnly"));
    if (Array.from(payload).length > MAX_MESSAGE_CHARS) {
      setAttachmentError(t("chat.messageTooLong").replace("{max}", String(MAX_MESSAGE_CHARS)));
      return;
    }
    const criteria = props.track === "code" && props.canSetCompletionCriteria
      ? completionDraft.split(/\r?\n/).map(text => text.trim()).filter(Boolean).map((text, index) => ({ id: `criterion-${index + 1}`, text }))
      : [];
    if (criteria.length > 12 || criteria.some(item => Array.from(item.text).length > 500)) {
      setAttachmentError(t("completion.inputLimit"));
      return;
    }
    sendingRef.current.add(sessionKey);
    const saved = saveChatDraft(draftScope, draftSessionKey, { draft, completionDraft, attachments });
    setDraftStorageErrors(current => ({ ...current, [composerKey]: !saved }));
    setSubmitting(true);
    // Clear on send like every other chat: a slow reply must not leave the box looking unsent.
    setDraft("");
    try {
      if (await props.onSend(payload, criteria.length ? criteria : undefined, attachments.length ? attachments : undefined, message || t("chat.attachmentOnly"))) {
        setAttachments([]);
        setCompletionDraft("");
        setAttachmentError(null);
        if (sessionRef.current === sessionKey) scrollToBottom();
        return;
      }
      // Rejected: hand the text back, but never overwrite something typed meanwhile.
      setDraft((current) => current || draft);
    } catch {
      setDraft((current) => current || draft);
    } finally {
      sendingRef.current.delete(sessionKey);
      setSubmitting(false);
    }
  };

  const fillIdea = (text: string) => {
    setDraft(text);
    inputRef.current?.focus();
  };
  const stop = async () => {
    if (stoppingRef.current.has(sessionKey)) return;
    stoppingRef.current.add(sessionKey);
    stopPendingRef.current.add(sessionKey);
    setStoppingSessions(current => new Set(current).add(sessionKey));
    try {
      const accepted = await (props.track === "cowork" ? props.onCancelCowork?.() : props.onStop());
      if (accepted === false) clearStopping(sessionKey);
    } catch { clearStopping(sessionKey); }
    finally {
      stopPendingRef.current.delete(sessionKey);
      setStoppingSessions(current => new Set(current));
    }
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

  const modeSelector = <ComposerMenu label={t("chat.mode.label")} disabled={busy || props.modeLocked} triggerClass={`chat-mode-select chat-mode-select--${props.mode}`} trigger={<><span>{t(`chat.mode.${props.mode}`)}</span><ChevronDown size={12} aria-hidden="true" /></>}>
    <div className="chat-popover-heading">{t("chat.mode.label")}</div>
    {MODES.map(mode => <button type="button" className="chat-mode-option" key={mode} disabled={busy || props.modeLocked} aria-pressed={props.mode === mode} onClick={event => { props.onModeChange(mode); closeComposerMenu(event.currentTarget); }}>
      <span className="chat-option-copy"><strong>{t(`chat.mode.${mode}`)}{mode === "standard" ? <small className="chat-recommended">{t("chat.recommended")}</small> : null}</strong><span>{t(`chat.mode.${mode}.hint`)}</span></span>
      {props.mode === mode ? <Check size={14} /> : null}
    </button>)}
  </ComposerMenu>;
  const contextEvent = props.events.filter(event => Array.isArray(event.details.context_paths) && typeof event.details.context_bytes === "number").at(-1);
  const contextPaths = ((contextEvent?.details.context_paths ?? []) as unknown[]).filter((path): path is string => typeof path === "string");
  const contextBytes = contextEvent?.details.context_bytes as number | undefined;
  const requestModelEvent = props.events.filter(event => typeof event.details.model === "string").at(-1);
  const pendingMessage = withAttachments(draft.trim() || (attachments.length ? t("chat.attachmentOnly") : ""));
  const inputBytes = new TextEncoder().encode(pendingMessage).length;
  const inputChars = Array.from(pendingMessage).length;
  const inputTooLong = inputChars > MAX_MESSAGE_CHARS;
  const formatBytes = (bytes: number) => bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
  const messageCount = (props.goal ? 1 : 0) + props.events.filter(event => event.action === "chat.user" || event.action === "chat.assistant").length;
  const contextPicker = <ComposerMenu label={t("chat.context")} className="chat-context" triggerClass="chat-context-trigger" trigger={<svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" className="chat-context-ring" /><circle cx="10" cy="10" r="7" className="chat-context-fill" pathLength="100" strokeDasharray={`${Math.min(100, inputChars / MAX_MESSAGE_CHARS * 100)} 100`} transform="rotate(-90 10 10)" /></svg>}>
    <div className="chat-popover-heading">{t("chat.context")}</div>
    <dl className="chat-context-stats"><div><dt>{t("chat.context.messages")}</dt><dd>{messageCount}</dd></div><div><dt>{t("chat.context.input")}</dt><dd>{inputChars.toLocaleString(language)} / {MAX_MESSAGE_CHARS.toLocaleString(language)}</dd></div><div><dt>{t("chat.context.inputBytes")}</dt><dd>{formatBytes(inputBytes)}</dd></div><div><dt>{t("chat.context.attachments")}</dt><dd>{attachments.length} / {MAX_ATTACHMENTS}</dd></div></dl>
    {repository ? <div className="chat-context-folder"><FolderOpen size={14} /><span title={repository.path}>{repository.name}</span>{!isCode ? <button type="button" onClick={event => { closeComposerMenu(event.currentTarget); props.onBrowseFolder(); }}>{t("chat.browseFolder")}</button> : null}</div> : null}
    {contextBytes !== undefined ? <div className="chat-context-files"><div className="chat-popover-heading">{t("chat.context.lastFiles")} · {contextPaths.length}</div><span>{(contextBytes / 1024).toFixed(1)} KB{props.contextLimit ? ` / ${(props.contextLimit / 1024).toFixed(0)} KB` : ""}</span>{contextEvent ? <time dateTime={contextEvent.created_at}>{new Date(contextEvent.created_at).toLocaleString(language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}</time> : null}{typeof contextEvent?.details.iteration_number === "number" ? <span>{t("chat.usage.iterations")} {contextEvent.details.iteration_number}</span> : null}<ul>{contextPaths.map(file => <li key={file} title={file}>{props.onOpenContextPath ? <button type="button" onClick={event => { closeComposerMenu(event.currentTarget); props.onOpenContextPath?.(file); }}>{file}</button> : file}</li>)}</ul></div> : <p className="chat-context-note">{t("chat.context.noFiles")}</p>}
    {requestModelEvent ? <div className="chat-context-model"><span>{t("chat.context.actualModel")}</span><strong>{String(requestModelEvent.details.model)}</strong>{typeof requestModelEvent.details.provider === "string" ? <small>{requestModelEvent.details.provider}</small> : null}</div> : null}
    {attachments.length ? <><div className="chat-popover-heading">{t("chat.context.pendingAttachments")}</div><ul className="chat-context-attachments">{attachments.map((file, index) => <li key={file.name + index}><Paperclip size={12} /><span>{file.name}</span><small>{formatBytes(new TextEncoder().encode(file.content).length)}</small></li>)}</ul></> : null}
    {isCode && props.budget ? <div className="chat-context-budget"><div className="chat-popover-heading">{t("chat.context.budget")}</div><BudgetMeter budget={props.budget} />{props.budget.exhausted_reason ? <p className="chat-usage-exhausted">{t(`chat.usage.reason.${props.budget.exhausted_reason}`)} {t("chat.usage.nextStep")}</p> : null}</div> : null}
    <p className="chat-context-note">{t("chat.context.note")}</p>
  </ComposerMenu>;
  const modelPicker = <ComposerMenu label={t("chat.modelChip")} className="chat-model-picker" triggerClass="chat-model-chip" disabled={busy || props.modelLocked} trigger={<><span>{props.modelName || t("chat.chooseModel")}</span><ChevronDown size={12} aria-hidden="true" /></>}>
    <div className="chat-popover-heading">{t("chat.modelChip")}</div>
    {props.models?.map(model => <button type="button" className="chat-model-option" key={model.id} disabled={busy || props.modelLocked || !props.onModelChange} aria-pressed={props.activeModelId === model.id} onClick={event => { props.onModelChange?.(model.id); closeComposerMenu(event.currentTarget); }}><span className="chat-option-copy"><strong>{model.label}</strong><span>{model.model}</span></span>{props.activeModelId === model.id ? <Check size={14} /> : null}</button>)}
    {!props.models?.length ? <p className="chat-popover-empty">{props.modelName || t("chat.noModels")}</p> : null}
    {props.onOpenModelSettings ? <button type="button" className="chat-model-settings" onClick={event => { closeComposerMenu(event.currentTarget); props.onOpenModelSettings?.(); }}><Settings2 size={14} />{t("settings.models.title")}</button> : null}
  </ComposerMenu>;
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
  ) : <span className="chat-folder-current" title={repository?.path}><FolderOpen size={15} />{isCode ? null : <span>{repository?.name ?? t("chat.noFolder")}</span>}</span>;
  const composerMeta = (
    <div className={`chat-composer-meta${isCode ? " chat-composer-meta--compact" : ""}`}>
      {folderPicker}
    </div>
  );
  const composerCard = (
    <div className="chat-composer-card" data-cds="ChatComposer">
      {attachments.length ? <div className="chat-attachments">{attachments.map((file, index) => <span className="chat-attachment" key={file.name + index}><Paperclip size={12} /><span title={file.name}>{file.name}</span><small>{formatBytes(new TextEncoder().encode(file.content).length)}</small><button type="button" aria-label={`${t("chat.removeAttachment")} ${file.name}`} onClick={() => setAttachments(current => current.filter((_, i) => i !== index))} disabled={busy}><X size={12} /></button></span>)}</div> : null}
      <textarea ref={inputRef} value={draft} onChange={event => setDraft(event.target.value)} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }} onKeyDown={event => {
        if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); }
      }} placeholder={t(isCode ? "chat.placeholder" : "chat.coworkPlaceholder")} rows={1} disabled={busy} aria-label={t("chat.subtitle")} aria-describedby={`${inputHintId}${attachmentError ? ` ${inputErrorId}` : ""}`} aria-invalid={inputTooLong || Boolean(attachmentError)} maxLength={MAX_MESSAGE_CHARS} />
      <div className="chat-composer-bar">
        <div className="chat-composer-controls">
          <input ref={fileRef} type="file" multiple hidden accept=".txt,.md,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.ini,.cfg,.sql,.sh,.css,.html,.java,.go,.rs,.rb,.php,.c,.h,.cpp" onChange={(event) => { void pickFiles(event.target.files); event.target.value = ""; }} />
          <button type="button" className="chat-attach" onClick={() => fileRef.current?.click()} disabled={busy || attachments.length >= MAX_ATTACHMENTS} aria-label={t("chat.attach")} title={t("chat.attach")}><Paperclip size={16} /></button>
          {composerMeta}
        </div>
        <div className="chat-composer-actions">
          {props.loopRunning || stopping || (props.track === "cowork" && props.sending && props.onCancelCowork) ? <button type="button" className="chat-send chat-send--stop" onClick={() => void stop()} disabled={stopping || (props.track === "code" && props.sending)} aria-label={t(stopping ? "chat.stopping" : "chat.stop")} title={t(stopping ? "chat.stopping" : "chat.stop")}><Square size={14} fill="currentColor" /></button> : <button type="button" className="chat-send" onClick={() => void send()} disabled={(!draft.trim() && !attachments.length) || busy || inputTooLong} aria-label={t("chat.send")} title={t("chat.send")}><ArrowUp size={18} /></button>}
        </div>
      </div>
      {isCode && props.canSetCompletionCriteria ? <details className="chat-completion-criteria">
        <summary>{t("completion.optional")}</summary>
        <label><span>{t("completion.lineHint")}</span><textarea value={completionDraft} onChange={event => setCompletionDraft(event.target.value)} rows={3} disabled={busy} aria-label={t("completion.optional")} /></label>
      </details> : null}
      {attachmentError ? <p id={inputErrorId} className="chat-attachment-error" role="alert">{attachmentError}</p> : null}
      {draftStorageErrors[composerKey] ? <p className="chat-draft-warning" role="status">{language === "zh-CN" ? "草稿未能保存到本机，重启后可能丢失；当前内容仍可发送。保留上限为 32 份草稿、合计 1 MB。" : "This draft could not be saved locally and may be lost after restart; you can still send it. Storage holds up to 32 drafts and 1 MB in total."}</p> : null}
      {readingAttachments ? <p className="chat-composer-hint" role="status">{t("chat.readingAttachments")}</p> : null}
      <div className={`chat-input-hint${inputTooLong ? " is-invalid" : ""}`} id={inputHintId}><span>{inputTooLong ? t("chat.messageTooLong").replace("{max}", String(MAX_MESSAGE_CHARS)) : t(stopping ? "chat.stopping" : props.sending ? isCode && !props.hasActiveRun ? "chat.creatingTask" : "chat.waitingReply" : "chat.hint")}</span>{attachments.length || inputChars >= MAX_MESSAGE_CHARS * .8 ? <span>{inputChars.toLocaleString(language)} / {MAX_MESSAGE_CHARS.toLocaleString(language)}</span> : null}</div>
    </div>
  );
  const composer = (
    <div className={`chat-composer chat-composer--${props.track}${isStart && !isCode ? " chat-composer--start" : ""}`}>
      {composerCard}
      <div className="chat-composer-footer"><div className="chat-composer-permissions">{isCode ? modeSelector : <span className="chat-readonly" title={t("chat.readonlyHint")}><LockKeyhole size={12} />{t("chat.readonly")}</span>}</div><div className="chat-composer-settings">{modelPicker}{contextPicker}</div></div>
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
      {turn.event ? <ChatEventDetails event={turn.event} onOpenFile={props.onOpenFile} /> : null}
    </div>
  );

  const renderTurn = (turn: ChatTurn) => {
    if (turn.role === "tool" || turn.role === "system") return renderActivity(turn);
    return <ChatMessage key={turn.id} turn={turn} onOpenFile={props.onOpenFile} live={busy && turn.status === "started" && turn.id === props.events.at(-1)?.id} />;
  };
  const showTaskState = isCode && props.hasActiveRun && (stopping || props.loopRunning || props.requiresRecovery || props.status === "paused" || props.status === "failed" || props.blockedReason || props.budget?.exhausted_reason || props.action === "manual_review" || props.action === "cancelled");
  const stateTitle = stopping ? t("chat.stopping") : props.requiresRecovery ? t("recovery.title") : props.budget?.exhausted_reason ? t("chat.usage.exhausted") : props.loopRunning ? t("chat.state.running") : props.action ? t(actionLabels[props.action]) : t(props.status === "paused" ? "paused" : "agent.manualReview");
  const stateHint = stopping ? t("chat.stoppingHint") : props.requiresRecovery ? t("chat.state.recoveryHint") : props.budget?.exhausted_reason ? `${t(`chat.usage.reason.${props.budget.exhausted_reason}`)} ${t("chat.usage.nextStep")}` : props.blockedReason || props.stateMessage || t(props.status === "paused" ? "chat.state.pausedHint" : props.status === "failed" ? "chat.state.failedHint" : "chat.state.runningHint");

  return (
    <section className={`chat-view chat-view--${props.track}${isStart ? " chat-view--empty" : ""}`}>
      <div className="chat-main">
        {props.error ? <div className="inline-error chat-load-error" role="alert"><span>{props.error}</span>{props.onRefresh ? <button type="button" className="small-action" disabled={props.sending} onClick={props.onRefresh}>{t("common.refresh")}</button> : null}</div> : null}
        {props.onPrepare ? <div className="chat-handoff"><Settings2 size={14} /><span>{t("chat.setupHint")}</span><button type="button" className="small-action" disabled={busy} onClick={props.onPrepare}>{t("chat.setupTitle")}</button></div> : null}
        {showTaskState ? <div className={`chat-task-state${props.requiresRecovery || props.blockedReason || props.budget?.exhausted_reason || props.status === "failed" ? " chat-task-state--attention" : ""}`} role="status">
          {stopping || props.loopRunning ? <Activity size={16} aria-hidden="true" /> : <AlertCircle size={16} aria-hidden="true" />}
          <div className="chat-task-state-copy"><strong>{stateTitle}</strong><span>{stateHint}</span>{!stopping && !props.requiresRecovery && !props.budget?.exhausted_reason && props.blockedReason && props.stateMessage && props.blockedReason !== props.stateMessage ? <span>{props.stateMessage}</span> : null}</div>
          <div className="chat-task-state-actions">{props.requiresRecovery && props.onRecover ? <button type="button" className="small-action" disabled={busy} onClick={() => void props.onRecover?.()}>{t("recovery.action")}</button>
            : props.status === "paused" && !props.blockedReason && !props.budget?.exhausted_reason && props.onResume ? <button type="button" className="small-action" disabled={busy} onClick={() => void props.onResume?.()}>{t("resume")}</button> : null}
          {!stopping && !props.loopRunning && props.onOpenRun ? <button type="button" className="small-action small-action--muted" onClick={props.onOpenRun}>{t("nav.runs")}</button> : null}</div>
        </div> : null}
        {isStart && isCode ? <>
          <div className="chat-code-welcome">
            <Code2 size={24} className="chat-empty-mark" />
            <h2>{t("chat.codeGreeting")}</h2>
            <p>{t(repository ? "chat.codeFolderReady" : "chat.codeChooseFolder")}</p>
            {repository ? <div className="chat-code-folder"><FolderOpen size={16} /><div><strong>{repository.name}</strong><span title={repository.path}>{repository.path}</span></div></div> : <button type="button" className="chat-open-folder" onClick={openFolder} disabled={busy}><FolderPlus size={16} />{t("folder.openLocal")}</button>}
          </div>
        </> : isStart ? (
          <div className="chat-start">
            <div className="chat-start-head">
              <Sparkles size={30} className="chat-empty-mark" />
              <h2 className="chat-empty-title">{t("chat.coworkGreeting")}</h2>
            </div>
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
            {props.onDelete && !props.loopRunning ? <div className="chat-header"><button className="small-action small-action--muted" onClick={() => void props.onDelete?.()} disabled={busy}><Trash2 size={13} /> {t("chat.delete")}</button></div> : null}

            <div className="chat-transcript-shell">
              <div className="chat-transcript" ref={containerRef} onScroll={updatePosition} onWheel={markUserInput} onTouchMove={markUserInput} onPointerDown={event => { if (event.target === event.currentTarget) markUserInput(); }} tabIndex={0} aria-label={t("chat.title")} onKeyDown={event => {
                if (event.target === event.currentTarget && ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) markUserInput();
              }}>
                <div className="chat-transcript-content" ref={contentRef}>
                  {items.map(item => {
                    if (item.kind !== "tools") return renderTurn(item.turn);
                    const failed = item.turns.filter(turn => turn.status === "failed").length;
                    return <Disclosure className="chat-tools" key={item.id} title={<span><Workflow size={13} /> {t("chat.usedTools").replace("{n}", String(item.turns.length))}{failed ? <span className="chat-tools-failed">{t("chat.failedTools").replace("{n}", String(failed))}</span> : null}</span>}>
                      <div className="chat-tools-list">{item.turns.map(renderActivity)}</div>
                    </Disclosure>;
                  })}
                  {isCode && props.agentState && props.onOpenFile && props.onOpenRun && props.onOpenValidation ? <ChatRunSummary key={props.agentState.task.id} state={props.agentState} onOpenFile={props.onOpenFile} onOpenRun={props.onOpenRun} onOpenValidation={props.onOpenValidation} onReviewCompletion={props.onOpenCompletion} busy={busy} /> : null}
                  {busy ? <div className="chat-working" role="status"><span className="timeline-quiet-dots" aria-hidden="true"><span /><span /><span /></span>{t(stopping ? "chat.stopping" : readingAttachments ? "chat.readingAttachments" : "chat.working")}</div> : null}
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
      {props.reviewPatch ? <div className="chat-handoff"><FileSearch size={14} /><span>{t("chat.reviewPatchHint")}</span><button className="small-action" onClick={props.onReviewPatch}>{t("chat.reviewPatch")}</button></div> : null}

          </>
        )}
        {composer}
      </div>
    </section>
  );
}
