import { useEffect, useRef, useState } from "react";
import { AsyncMessage } from "@coding-agent/ui";
import { ChevronDown, ChevronRight, FileCode2, Folder, Search, RefreshCw, Pencil } from "lucide-react";
import { getRepositoryContext, getRepositoryTree, readSourceFile, searchRepository, type Repository, type RepositoryContext, type SearchMatch, type SourceFile, type TreeEntry } from "../lib/api";
import { usePreferences } from "../lib/preferences";

type Props = {
  repositories: Repository[];
  repositoryId: string;
  onRepositoryChange: (id: string) => void;
  onEdit: (file: SourceFile) => void;
  canEdit: boolean;
  initialFile?: { path: string; line: number } | null;
};

export function RepositoryBrowser({ repositories, repositoryId, onRepositoryChange, onEdit, canEdit, initialFile }: Props) {
  const { t } = usePreferences();
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [context, setContext] = useState<RepositoryContext | null>(null);
  const [file, setFile] = useState<SourceFile | null>(null);
  const [expanded, setExpanded] = useState(new Set(["src", "app", "apps", "tests"]));
  const [query, setQuery] = useState("");
  const [glob, setGlob] = useState("");
  const [matches, setMatches] = useState<SearchMatch[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fileLoading, setFileLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [selectedLine, setSelectedLine] = useState(1);
  const fileRequest = useRef<AbortController | null>(null);
  const searchRequest = useRef<AbortController | null>(null);
  const source = useRef<HTMLPreElement>(null);

  useEffect(() => {
    fileRequest.current?.abort();
    searchRequest.current?.abort();
    setFile(null); setMatches(null); setContext(null); setEntries([]); setError(null);
    setFileLoading(false); setSearching(false);
    if (!repositoryId) return;
    const controller = new AbortController();
    setLoading(true);
    Promise.all([getRepositoryTree(repositoryId, controller.signal), getRepositoryContext(repositoryId, controller.signal)])
      .then(([tree, summary]) => {
        if (controller.signal.aborted) return;
        setEntries(tree.items.sort((a, b) => a.path.localeCompare(b.path)));
        setContext(summary); setTruncated(tree.truncated);
      })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : t("common.error")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); fileRequest.current?.abort(); searchRequest.current?.abort(); };
  }, [repositoryId, revision]);

  const openFile = async (name: string, line = 1) => {
    fileRequest.current?.abort();
    const controller = new AbortController(); fileRequest.current = controller;
    setFileLoading(true); setError(null);
    try {
      const result = await readSourceFile(repositoryId, name, controller.signal);
      if (!controller.signal.aborted) { setFile(result); setSelectedLine(line); }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : t("common.error"));
    } finally { if (!controller.signal.aborted) setFileLoading(false); }
  };

  useEffect(() => {
    if (initialFile && repositoryId) void openFile(initialFile.path, initialFile.line);
  }, [repositoryId, initialFile]);

  useEffect(() => {
    source.current?.querySelector('[data-line="' + selectedLine + '"]')?.scrollIntoView({ block: "nearest" });
  }, [file, selectedLine]);

  const search = async () => {
    if (!query.trim() || !repositoryId) return;
    searchRequest.current?.abort();
    const controller = new AbortController(); searchRequest.current = controller;
    setSearching(true); setError(null);
    try {
      const result = await searchRepository(repositoryId, query.trim(), glob.trim(), controller.signal);
      if (!controller.signal.aborted) { setMatches(result.matches); setTruncated(result.truncated); }
    } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : t("common.error")); }
    finally { if (!controller.signal.aborted) setSearching(false); }
  };

  const visibleEntries = entries.filter(entry => {
    const parts = entry.path.split("/");
    return parts.slice(0, -1).every((_, index) => expanded.has(parts.slice(0, index + 1).join("/")));
  });

  return <section className="repository-workspace" aria-label={t("browser.title")}>
    <div className="workspace-heading"><div><div className="section-kicker">SOURCE / EXPLORER</div><h1>{t("browser.title")}</h1><p>{t("browser.subtitle")}</p></div>
      <div className="workspace-controls"><label><span className="sr-only">{t("browser.repository")}</span><select value={repositoryId} onChange={event => onRepositoryChange(event.target.value)}>{!repositories.length ? <option value="">{t("noRepository")}</option> : repositories.map(repository => <option value={repository.id} key={repository.id}>{repository.name} · {repository.branch}</option>)}</select></label><button className="icon-button" title={t("common.refresh")} aria-label={t("common.refresh")} disabled={loading || !repositoryId} onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} /></button></div>
    </div>
    {!repositories.length ? <div className="panel browser-empty">{t("browser.empty")}</div> : <>
      <form className="browser-search" onSubmit={event => { event.preventDefault(); void search(); }}><Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder={t("browser.query")} aria-label={t("browser.query")} /><input className="glob-input" value={glob} onChange={event => setGlob(event.target.value)} placeholder={t("browser.glob")} aria-label={t("browser.glob")} /><button className="small-action" disabled={!query.trim() || searching}>{searching ? t("common.loading") : t("browser.search")}</button></form>
      {error ? <AsyncMessage tone="error" onRetry={() => setRevision(value => value + 1)} retryLabel={t("common.retry")}>{error}</AsyncMessage> : null}
      {context ? <div className="repository-context"><div><span>{t("browser.context")}</span><strong>{Object.keys(context.languages).join(" · ") || t("common.none")}</strong><small>{context.file_count} {t("files")} · {context.package_manager || t("common.none")}</small></div><div><span>{t("browser.entries")}</span>{context.entry_files.slice(0, 4).map(name => <button className="path-link" key={name} onClick={() => void openFile(name)}>{name}</button>)}</div><div><span>{t("browser.tests")}</span><small>{context.test_directories.join(", ") || t("common.none")}</small><span>{t("browser.config")}</span><small>{context.config_files.slice(0, 4).join(", ") || t("common.none")}</small></div></div> : null}
      {truncated ? <p className="output-note" role="status">{t("browser.truncated")}</p> : null}
      <div className="source-layout panel"><aside className="source-sidebar"><div className="source-section-title">{matches === null ? t("browser.files") : t("browser.results")} {matches !== null ? <button className="text-button" onClick={() => setMatches(null)}>{t("browser.files")}</button> : null}</div>
        <div className="source-tree" aria-label={matches === null ? t("browser.files") : t("browser.results")}>{loading ? <p className="browser-empty" role="status">{t("common.loading")}</p> : matches !== null ? matches.length ? matches.map((match, index) => <button className="search-match" key={match.path + ":" + match.line + ":" + index} onClick={() => void openFile(match.path, match.line)}><strong>{match.path}:{match.line}</strong><span>{match.text}</span></button>) : <p className="browser-empty">{t("browser.noMatches")}</p> : visibleEntries.map(entry => <button key={entry.path} className={"tree-entry" + (entry.path === file?.path ? " is-selected" : "")} style={{ paddingLeft: 12 + (entry.path.split("/").length - 1) * 14 }} aria-expanded={entry.kind === "directory" ? expanded.has(entry.path) : undefined} onClick={() => {
          if (entry.kind === "file") void openFile(entry.path);
          else setExpanded(current => { const next = new Set(current); if (next.has(entry.path)) next.delete(entry.path); else next.add(entry.path); return next; });
        }}>{entry.kind === "directory" ? <>{expanded.has(entry.path) ? <ChevronDown size={12} /> : <ChevronRight size={12} />}<Folder size={14} /></> : <FileCode2 size={14} />}<span>{entry.name}</span></button>)}</div>
        {file ? <div className="source-symbols"><div className="source-section-title">{t("browser.symbols")}</div>{file.symbols.length ? file.symbols.map(symbol => <button className="symbol-row" key={symbol.name + symbol.line} onClick={() => setSelectedLine(symbol.line)}><span>{symbol.name}</span><small>{symbol.kind} :{symbol.line}</small></button>) : <p className="browser-empty">{t("browser.noSymbols")}</p>}</div> : null}
      </aside><div className="source-view" aria-busy={fileLoading}>{fileLoading ? <div className="browser-empty" role="status">{t("common.loading")}</div> : file ? <><div className="source-toolbar"><strong>{file.path}</strong><span>{file.language} · {file.line_count} {t("browser.line")}</span><button className="small-action" disabled={!canEdit} onClick={() => onEdit(file)}><Pencil size={13} />{t("browser.edit")}</button></div><pre className="source-code" ref={source} tabIndex={0} aria-label={file.path}><code>{file.content.split("\n").slice(0, 3000).map((line, index) => <span key={index} data-line={index + 1} className={"source-line" + (index + 1 === selectedLine ? " selected-line" : "")}><span className="line-number" aria-hidden="true">{index + 1}</span><span>{line || " "}</span></span>)}</code></pre>{file.line_count > 3000 ? <p className="output-note">{t("browser.large")}</p> : null}</> : <div className="browser-empty source-placeholder"><FileCode2 size={30} /><span>{t("browser.choose")}</span></div>}</div></div>
    </>}
  </section>;
}
