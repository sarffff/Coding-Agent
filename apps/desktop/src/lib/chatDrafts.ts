export type ChatAttachment = { name: string; content: string };
export type ChatDraft = { draft: string; completionDraft: string; attachments: ChatAttachment[] };

const MAX_STORED_BYTES = 1024 * 1024;
const MAX_STORED_DRAFTS = 32;
const storageKey = (scope: string) => `forge.chat-drafts.v1:${encodeURIComponent(scope)}`;
const activeDraftKey = (scope: string) => `forge.chat-new-draft.v1:${encodeURIComponent(scope)}`;

export function createNewChatDraftId(scope: string): string {
  const id = crypto.randomUUID();
  try { window.localStorage.setItem(activeDraftKey(scope), id); }
  catch { /* The composer reports failed draft persistence without blocking input. */ }
  return id;
}

export function getNewChatDraftId(scope: string): string {
  try {
    const id = window.localStorage.getItem(activeDraftKey(scope));
    if (id && /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(id)) return id;
  } catch { /* A fresh in-memory draft still works when storage is unavailable. */ }
  return createNewChatDraftId(scope);
}

function readDrafts(scope: string): Record<string, ChatDraft> {
  const raw = window.localStorage.getItem(storageKey(scope));
  if (!raw) return {};
  if (new TextEncoder().encode(raw).length > MAX_STORED_BYTES) throw new Error("Draft storage limit");
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid draft storage");
  const drafts: Record<string, ChatDraft> = {};
  for (const [key, item] of Object.entries(value)) {
    if (!item || typeof item !== "object" || typeof item.draft !== "string" || typeof item.completionDraft !== "string" || !Array.isArray(item.attachments)) continue;
    if (item.draft.length > 32_000 || item.completionDraft.length > 6_100 || item.attachments.length > 5) continue;
    if (!item.attachments.every((file: unknown) => file && typeof file === "object" && "name" in file && "content" in file && typeof file.name === "string" && typeof file.content === "string" && new TextEncoder().encode(file.content).length <= 64 * 1024)) continue;
    drafts[key] = { draft: item.draft, completionDraft: item.completionDraft, attachments: item.attachments };
  }
  return drafts;
}

export function loadChatDraft(scope: string, key: string): ChatDraft | null {
  try { return readDrafts(scope)[key] ?? null; }
  catch { return null; }
}

function writeDrafts(scope: string, drafts: Record<string, ChatDraft>): boolean {
  const raw = JSON.stringify(drafts);
  if (Object.keys(drafts).length > MAX_STORED_DRAFTS || new TextEncoder().encode(raw).length > MAX_STORED_BYTES) return false;
  if (Object.keys(drafts).length) window.localStorage.setItem(storageKey(scope), raw);
  else window.localStorage.removeItem(storageKey(scope));
  return true;
}

export function saveChatDraft(scope: string, key: string, draft: ChatDraft): boolean {
  try {
    const drafts = readDrafts(scope);
    if (!draft.draft && !draft.completionDraft && !draft.attachments.length) delete drafts[key];
    else drafts[key] = { draft: draft.draft, completionDraft: draft.completionDraft, attachments: draft.attachments };
    return writeDrafts(scope, drafts);
  } catch { return false; }
}

export function moveChatDraft(scope: string, sourceKey: string, targetKey: string, draft: ChatDraft): boolean {
  try {
    const drafts = readDrafts(scope);
    delete drafts[sourceKey];
    if (draft.draft || draft.completionDraft || draft.attachments.length) drafts[targetKey] = { draft: draft.draft, completionDraft: draft.completionDraft, attachments: draft.attachments };
    return writeDrafts(scope, drafts);
  } catch { return false; }
}

export function removeChatDraft(scope: string, key: string): boolean {
  return saveChatDraft(scope, key, { draft: "", completionDraft: "", attachments: [] });
}
