import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Bot, Check, ChevronDown, Code2, FileSearch, FolderOpen, FolderPlus, GitBranch, Monitor, Rocket, ShieldCheck, Sparkles, Square, Workflow, X } from "lucide-react";
import type { AgentMode, Approval, AuditEvent, TaskBudget, TaskTrack } from "../lib/api";
import { usePreferences, type Language } from "../lib/preferences";

type ChatRole = "user" | "assistant" | "tool" | "system";

type ChatTurn = {
  id: string;
  role: ChatRole;
  title: string;
  body?: string;
  bodyPre?: boolean;
  steps?: string[];
  collapse?: boolean;
  status?: "started" | "succeeded" | "failed";
  time: string;
};

type ChatItem = { kind: "turn"; turn: ChatTurn } | { kind: "tools"; id: string; turns: ChatTurn[] };

type ChatPlan = { objective: string; steps: Array<{ id: string; title: string }> } | undefined;

type ChatPanelProps = {
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
  error: string | null;
  mode: AgentMode;
  modeLocked: boolean;
  onModeChange: (mode: AgentMode) => void;
  pending: Approval | null;
  onDecide: (approvalId: string, decision: "approve" | "reject", reason?: string) => void | Promise<void>;
  budget: TaskBudget | null;
};

const MODES: AgentMode[] = ["conservative", "standard", "autonomous"];

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
  if (a.startsWith("patch.") || a.startsWith("tests.") || a.startsWith("git.") || a.startsWith("checkpoint.")) return "tool";
  return "system";
}

export function ChatPanel(props: ChatPanelProps) {
  const { t, language } = usePreferences();
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
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
      turns.push({
        id: event.id,
        role,
        title: event.action === "patch.draft" ? t("chat.thinking") : event.summary,
        body: rationale || failed || undefined,
        bodyPre: Boolean(failed),
        collapse: Boolean(rationale || failed),
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

  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [items.length, props.loopRunning]);

  const terminal = !props.hasActiveRun || ["done", "completed", "cancelled"].includes(props.status ?? "");
  const repository = props.repositories.find(item => item.id === props.selectedRepositoryId);
  const readyToPublish = repository?.has_git === true && (props.status === "ready_for_pr" || props.status === "pr_created");
  const localReady = repository?.has_git === false && props.status === "ready_for_pr";
  const busy = props.loopRunning || props.sending;
  const send = async () => {
    const message = draft.trim();
    if (!message || busy) return;
    if (await props.onSend(message)) {
      setDraft("");
      if (inputRef.current) inputRef.current.style.height = "auto";
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
    <div className="chat-mode">
      {props.modeLocked ? (
        <span className={`chat-mode-chip chat-mode-chip--${props.mode}`}><span className="chat-mode-dot" />{t(`chat.mode.${props.mode}`)}</span>
      ) : MODES.map((m) => (
        <button key={m} type="button" className={`chat-mode-opt chat-mode-opt--${m} ${props.mode === m ? "is-active" : ""}`} onClick={() => props.onModeChange(m)} disabled={busy} aria-pressed={props.mode === m} title={t(`chat.mode.${m}.hint`)}>
          <span className="chat-mode-dot" />{t(`chat.mode.${m}`)}
        </button>
      ))}
    </div>
  );
  const folderPicker = terminal ? (
    <details className="folder-picker" ref={folderPickerRef} onKeyDown={event => {
      if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
    }}>
      <summary className="folder-picker-trigger" aria-disabled={busy} onClick={event => { if (busy) event.preventDefault(); }}>
        <FolderOpen size={16} />
        <span className="folder-picker-label"><span>{t("chat.projectFolder")}</span><strong>{repository?.name ?? t(isCode ? "chat.chooseFolder" : "chat.noFolder")}</strong></span>
        <ChevronDown size={14} />
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
  ) : <span className="chat-folder-current" title={repository?.path}><FolderOpen size={15} />{repository?.name ?? t("chat.noFolder")}</span>;
  const composerMeta = (
    <div className="chat-composer-meta">
      {folderPicker}
      {isCode && repository ? <span className={`chat-folder-chip chat-folder-chip--${repository.has_git ? "git" : "local"}`} title={t(repository.has_git ? "chat.gitFolderHint" : "chat.localFolderHint")}>{repository.has_git ? <GitBranch size={13} /> : <Monitor size={13} />}{repository.has_git ? "Git" : "Local"}</span> : null}
      {repository ? <button type="button" className="chat-folder-browse" onClick={props.onBrowseFolder} title={repository.path}><FileSearch size={14} /><span>{t("chat.browseFolder")}</span></button> : null}
    </div>
  );
  const composerCard = (
    <div className="chat-composer-card">
      <textarea ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} onInput={(event) => { const el = event.currentTarget; el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 200)}px`; }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} placeholder={t(isCode ? "chat.placeholder" : "chat.coworkPlaceholder")} rows={isCode ? 2 : 3} disabled={busy} aria-label={t("chat.subtitle")} />
      <div className="chat-composer-bar">
        <span className="chat-composer-hint">{t("chat.hint")}</span>
        <button className="chat-send" onClick={() => void send()} disabled={!draft.trim() || busy} aria-label={t("chat.send")}><ArrowUp size={16} /></button>
      </div>
    </div>
  );
  const composer = (
    <div className={`chat-composer chat-composer--${props.track}${isStart && !isCode ? " chat-composer--start" : ""}`}>
      {isCode ? composerMeta : null}
      {composerCard}
      {isCode ? <div className="chat-composer-footer">{modeSelector}</div> : composerMeta}
    </div>
  );

  const renderActivity = (turn: ChatTurn) => (
    <div className="chat-activity" key={turn.id}>
      <div className="chat-activity-row">
        <Workflow size={13} className="chat-head-icon" />
        <span className="chat-activity-text">{turn.title}</span>
        {turn.time ? <span className="chat-time">{turn.time}</span> : null}
        {turn.status === "failed" ? <span className="chat-dot chat-dot--failed" /> : null}
      </div>
      {turn.body ? <details className="chat-collapse"><summary>{t("chat.details")}</summary><pre className="chat-bubble-body">{turn.body}</pre></details> : null}
    </div>
  );

  const renderTurn = (turn: ChatTurn) => {
    if (turn.role === "user") return <div className="chat-turn chat-turn--user" key={turn.id}><div className="chat-bubble">{turn.title}</div></div>;
    if (turn.role === "tool" || turn.role === "system") return renderActivity(turn);
    return (
      <div className="chat-turn chat-turn--assistant" key={turn.id}>
        {turn.steps ? (
          <>
            <div className="chat-msg-label">{turn.title}</div>
            <ol className="chat-plan">{turn.steps.map((step, index) => <li key={index}>{step}</li>)}</ol>
          </>
        ) : turn.collapse && turn.body ? (
          <details className="chat-collapse"><summary>{turn.title}</summary><div className="chat-prose">{turn.body}</div></details>
        ) : (
          <>
            <div className="chat-msg">{turn.title}</div>
            {turn.body ? (turn.bodyPre ? <pre className="chat-bubble-body">{turn.body}</pre> : <div className="chat-prose">{turn.body}</div>) : null}
          </>
        )}
      </div>
    );
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
        {props.loopRunning ? <button className="small-action small-action--muted" onClick={() => void props.onStop()}><Square size={13} /> {t("chat.stop")}</button> : <span className="chat-state">{terminal ? t("chat.idle") : t("chat.active")}</span>}
      </div>

      {props.budget ? (
        <div className="chat-usage">
          <span>{t("chat.usage.iterations")} {props.budget.iterations}/{props.budget.max_iterations}</span>
          <span>{t("chat.usage.calls")} {props.budget.remote_calls}/{props.budget.max_remote_calls}</span>
          {props.budget.exhausted_reason ? <span className="chat-usage-exhausted">{t("chat.usage.exhausted")}</span> : null}
        </div>
      ) : null}

            <div className="chat-transcript" ref={scrollRef}>
              {items.map((item) => item.kind === "tools" ? (
                <details className="chat-tools" key={item.id}>
                  <summary><Workflow size={13} /> {t("chat.usedTools").replace("{n}", String(item.turns.length))}</summary>
                  <div className="chat-tools-list">{item.turns.map(renderActivity)}</div>
                </details>
              ) : renderTurn(item.turn))}
              {busy ? <div className="chat-working"><span className="stream-spinner" /> {t("chat.working")}</div> : null}
            </div>

      {pending ? (
        <div className={`chat-approval chat-approval--${pending.type}`}>
          <div className="chat-approval-head"><ShieldCheck size={14} /> {t(`chat.approval.${pending.type}`)}</div>
          <div className="chat-approval-summary">{pending.summary}</div>
          <div className="chat-approval-actions">
            <button className="chat-approve" disabled={busy} onClick={() => void props.onDecide(pending.id, "approve")}><Check size={14} /> {t("chat.approve")}</button>
            <button className="chat-reject" disabled={busy} onClick={() => void props.onDecide(pending.id, "reject", t("chat.rejectReason"))}><X size={14} /> {t("chat.reject")}</button>
          </div>
        </div>
      ) : null}

      {readyToPublish ? <div className="chat-handoff"><Rocket size={14} /><span>{t("chat.handoff")}</span><button className="small-action" onClick={props.onOpenPublish}>{t("chat.openPublish")}</button></div> : null}
      {localReady ? <div className="chat-handoff"><FileSearch size={14} /><span>{t("chat.localComplete")}</span><button className="small-action" onClick={props.onReviewPatch}>{t("chat.reviewLocal")}</button></div> : null}
      {props.reviewPatch ? <div className="chat-handoff"><FileSearch size={14} /><span>{t("chat.reviewPatchHint")}</span><button className="small-action" onClick={props.onReviewPatch}>{t("chat.reviewPatch")}</button></div> : null}

            {composer}
          </>
        )}
      </div>
    </section>
  );
}
