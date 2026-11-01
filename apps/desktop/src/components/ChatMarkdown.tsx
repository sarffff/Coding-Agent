import { createElement, useEffect, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { usePreferences } from "../lib/preferences";
import "../styles/chat-markdown.css";

function inline(text: string): ReactNode[] {
  return text.split(/(\\[\\`*_[\]()|]|`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|(?<!\w)_[^_\n]+_(?!\w)|\[[^\]\n]+\]\([^)\s]+\))/g).map((part, index) => {
    if (/^\\[\\`*_[\]()|]$/.test(part)) return part.slice(1);
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) return <em key={index}>{part.slice(1, -1)}</em>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link && /^(https?:\/\/|mailto:)/i.test(link[2])) return <a key={index} href={link[2]} target="_blank" rel="noopener noreferrer">{link[1]}</a>;
    return part;
  });
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const { t, language: interfaceLanguage } = usePreferences();
  const copyLabel = interfaceLanguage === "zh-CN" ? "复制代码" : "Copy code";
  const languageLabel = language || (interfaceLanguage === "zh-CN" ? "纯文本" : "Plain text");
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (copied === "idle") return;
    const timer = window.setTimeout(() => setCopied("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied("copied"); }
    catch { setCopied("failed"); }
  };
  return <div className="chat-code-block">
    <div className="chat-code-header"><span>{languageLabel}</span><button type="button" onClick={() => void copy()} aria-label={copyLabel} title={copyLabel}>{copied === "copied" ? <Check size={13} /> : <Copy size={13} />}{copyLabel}</button>{copied !== "idle" ? <span role="status">{t(copied === "copied" ? "chat.copied" : "chat.copyError")}</span> : null}</div>
    <pre tabIndex={0} aria-label={languageLabel}><code>{code}</code></pre>
  </div>;
}

function cells(line: string): string[] {
  const result: string[] = [];
  let cell = "", code = false;
  const value = line.trim();
  for (let index = 0; index < value.length; index++) {
    const character = value[index];
    if (character === "\\" && index + 1 < value.length) {
      const next = value[++index];
      cell += next === "|" ? next : character + next;
    } else if (character === "`") { code = !code; cell += character; }
    else if (character === "|" && !code) { result.push(cell.trim()); cell = ""; }
    else cell += character;
  }
  result.push(cell.trim());
  if (value.startsWith("|") && result[0] === "") result.shift();
  if (result.at(-1) === "" && value.endsWith("|")) result.pop();
  return result;
}
const isList = (line: string) => /^\s*(?:[-*]|\d+\.)\s+/.test(line);
const isHeading = (line: string) => /^#{1,6}\s+/.test(line);
const isQuote = (line: string) => /^\s*>/.test(line);
const fenceStart = (line: string) => line.match(/^\s*(`{3,}|~{3,})([^`~]*)$/);
const isTable = (lines: string[], index: number) => lines[index].includes("|") && index + 1 < lines.length && cells(lines[index]).length > 0 && cells(lines[index]).length === cells(lines[index + 1]).length && cells(lines[index + 1]).every(cell => /^:?-{3,}:?$/.test(cell));

// A bounded Markdown subset for model replies. React renders all raw HTML as text.
export function ChatMarkdown({ text }: { text: string }) {
  const { language } = usePreferences();
  const lines = text.split(/\r?\n/);
  const blocks: ReactNode[] = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    const key = index;
    const fence = fenceStart(line);
    if (fence) {
      const content: string[] = [];
      const close = new RegExp(`^\\s*${fence[1][0]}{${fence[1].length},}\\s*$`);
      index++;
      while (index < lines.length && !close.test(lines[index])) content.push(lines[index++]);
      if (index < lines.length) index++;
      blocks.push(<CodeBlock key={key} language={fence[2].trim().split(/\s+/)[0]} code={content.join("\n")} />);
      continue;
    }
    if (isQuote(line)) {
      const quote: string[] = [];
      while (index < lines.length && isQuote(lines[index])) quote.push(lines[index++].replace(/^\s*> ?/, ""));
      blocks.push(<blockquote key={key}><p>{inline(quote.join("\n"))}</p></blockquote>);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      blocks.push(createElement(`h${heading[1].length}`, { key }, inline(heading[2])));
      index++;
      continue;
    }
    if (isList(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const start = ordered ? Number(line.trim().match(/^\d+/)?.[0]) : 1;
      const items: ReactNode[] = [];
      while (index < lines.length && isList(lines[index]) && /^\s*\d+\./.test(lines[index]) === ordered) {
        const item = lines[index++].replace(/^\s*(?:[-*]|\d+\.)\s+/, "");
        const task = item.match(/^\[([ xX])\]\s+(.+)$/);
        items.push(task ? <li className="chat-task-item" key={index}><input type="checkbox" checked={task[1] !== " "} disabled aria-label={task[2]} /><span>{inline(task[2])}</span></li> : <li key={index}>{inline(item)}</li>);
      }
      blocks.push(ordered ? <ol key={key} start={start === 1 ? undefined : start}>{items}</ol> : <ul key={key}>{items}</ul>);
      continue;
    }
    if (isTable(lines, index)) {
      const header = cells(line);
      const alignment = cells(lines[index + 1]).map(cell => cell.endsWith(":") ? cell.startsWith(":") ? "center" : "right" : cell.startsWith(":") ? "left" : undefined);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|")) rows.push(cells(lines[index++]));
      blocks.push(<div className="chat-table-scroll" key={key} tabIndex={0} role="region" aria-label={language === "zh-CN" ? "表格，可横向滚动" : "Table, scroll horizontally"}><table><thead><tr>{header.map((cell, column) => <th key={column} scope="col" style={{ textAlign: alignment[column] }}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{header.map((_, column) => <td key={column} style={{ textAlign: alignment[column] }}>{inline(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    const paragraph = [line];
    index++;
    while (index < lines.length && lines[index].trim() && !isHeading(lines[index]) && !isList(lines[index]) && !isQuote(lines[index]) && !fenceStart(lines[index]) && !isTable(lines, index)) paragraph.push(lines[index++]);
    blocks.push(<p key={key}>{inline(paragraph.join("\n"))}</p>);
  }
  return <div className="chat-prose chat-markdown">{blocks}</div>;
}
