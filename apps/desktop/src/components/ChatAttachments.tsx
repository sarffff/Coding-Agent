import { Paperclip } from "lucide-react";
import type { ChatAttachment } from "../lib/chatDrafts";
import { usePreferences } from "../lib/preferences";

export function readChatAttachments(value: unknown): ChatAttachment[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(file => file && typeof file === "object" && typeof file.name === "string" && typeof file.content === "string" ? [{ name: file.name, content: file.content }] : []).slice(0, 5);
}

export function ChatAttachments({ attachments }: { attachments: ChatAttachment[] }) {
  const { language } = usePreferences();
  if (!attachments.length) return null;
  return <div className="chat-sent-attachments" aria-label={language === "zh-CN" ? "已发送附件" : "Sent attachments"}>
    {attachments.map((file, index) => {
      const bytes = new TextEncoder().encode(file.content).length;
      return <details className="chat-sent-attachment" key={file.name + index}>
        <summary><Paperclip size={14} aria-hidden="true" /><span title={file.name}>{file.name}</span><small>{bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`}</small></summary>
        <pre tabIndex={0} aria-label={file.name}>{file.content}</pre>
      </details>;
    })}
  </div>;
}
