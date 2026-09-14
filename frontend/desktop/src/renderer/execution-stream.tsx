import { useState, type JSX } from "react";
import { Icon } from "./icons.tsx";
import {
  extractExecutionItems,
  groupExecutionItems,
  type ExecutionItem
} from "./execution-stream-model.ts";
import type { LocalEvent, Task } from "./api.ts";
import { normalizeConversationMessage } from "./conversation-presentation.ts";
import { AssistantMarkdown } from "./assistant-markdown-renderer";

/**
 * Component to render a single Command/Shell execution box in CC terminal style.
 */
export function CommandCard({ item }: { item: Extract<ExecutionItem, { kind: "command" }> }): JSX.Element {
  const [expanded, setExpanded] = useState(true);
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    void navigator.clipboard.writeText(item.command);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <article className={`agent-tool-card tool-command ${item.status}`}>
      <div className="tool-card-header" onClick={() => setExpanded(!expanded)} role="button" tabIndex={0}>
        <div className="tool-card-title">
          <Icon name="activity" size={15} />
          <span className="command-prefix">执行:</span>
          <code className="command-text">{item.command}</code>
        </div>
        <div className="tool-card-meta">
          {item.durationMs ? <small className="tool-duration">{item.durationMs}ms</small> : null}
          {item.exitCode !== undefined ? (
            <span className={`exit-badge ${item.exitCode === 0 ? "success" : "failed"}`}>
              {item.exitCode === 0 ? "已完成" : `失败 (${item.exitCode})`}
            </span>
          ) : null}
          <button
            type="button"
            className="tool-copy-btn"
            onClick={handleCopy}
            title="复制操作内容"
            aria-label="复制操作内容"
          >
            <Icon name={copied ? "check" : "more"} size={13} />
          </button>
          <span className={`collapse-indicator ${expanded ? "open" : ""}`}>
            <Icon name="arrow" size={13} />
          </span>
        </div>
      </div>
      {expanded && item.output ? (
        <div className="tool-card-body">
          <pre className="terminal-output">
            <code>{item.output}</code>
          </pre>
        </div>
      ) : null}
    </article>
  );
}

/**
 * Component to render a Unified Diff code block with syntax line highlighting.
 */
export function DiffCard({ item }: { item: Extract<ExecutionItem, { kind: "diff" }> }): JSX.Element {
  const [expanded, setExpanded] = useState(true);
  const [opening, setOpening] = useState(false);
  const [openStatus, setOpenStatus] = useState<string | null>(null);
  const parsed = item.parsed;

  const handleOpenFile = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!item.filePath || !window.assistantDesktop?.openPath) return;
    setOpening(true);
    setOpenStatus(null);
    try {
      const err = await window.assistantDesktop.openPath(item.filePath);
      if (err) {
        setOpenStatus("打开失败");
        setTimeout(() => setOpenStatus(null), 3000);
      } else {
        setOpenStatus("已在系统打开");
        setTimeout(() => setOpenStatus(null), 2000);
      }
    } catch {
      setOpenStatus("打开失败");
      setTimeout(() => setOpenStatus(null), 3000);
    } finally {
      setOpening(false);
    }
  };

  return (
    <article className="agent-tool-card tool-diff">
      <div className="tool-card-header" onClick={() => setExpanded(!expanded)} role="button" tabIndex={0}>
        <div className="tool-card-title">
          <Icon name="file" size={15} />
          <span className="diff-file-path">{item.filePath}</span>
        </div>
        <div className="tool-card-meta">
          {parsed.additions > 0 ? <span className="diff-stat-add">+{parsed.additions}</span> : null}
          {parsed.deletions > 0 ? <span className="diff-stat-del">-{parsed.deletions}</span> : null}
          {item.filePath ? (
            <button
              type="button"
              className="tool-open-btn"
              onClick={handleOpenFile}
              disabled={opening}
              title={openStatus || `在系统中打开 ${item.filePath}`}
              aria-label={`在系统中打开 ${item.filePath}`}
            >
              <Icon name="external" size={13} />
              <span>{openStatus || (opening ? "打开中…" : "在系统中打开")}</span>
            </button>
          ) : null}
          <span className={`collapse-indicator ${expanded ? "open" : ""}`}>
            <Icon name="arrow" size={13} />
          </span>
        </div>
      </div>
      {expanded ? (
        <div className="tool-card-body diff-viewer-body">
          {parsed.chunks.length ? (
            <div className="diff-chunks">
              {parsed.chunks.map((chunk, cIdx) => (
                <div className="diff-chunk" key={cIdx}>
                  <div className="diff-chunk-header">{chunk.header}</div>
                  <div className="diff-lines">
                    {chunk.lines.map((line, lIdx) => (
                      <div className={`diff-line diff-line-${line.type}`} key={lIdx}>
                        <span className="diff-line-num old-num">{line.oldLineNumber ?? ""}</span>
                        <span className="diff-line-num new-num">{line.newLineNumber ?? ""}</span>
                        <span className="diff-line-sign">
                          {line.type === "add" ? "+" : line.type === "delete" ? "-" : " "}
                        </span>
                        <code className="diff-line-content">{line.content}</code>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <pre className="terminal-output">
              <code>{item.diffText}</code>
            </pre>
          )}
        </div>
      ) : null}
    </article>
  );
}

/**
 * Component to render Thinking / Reasoning process in collapsible drawer.
 */
export function ThoughtCard({ item }: { item: Extract<ExecutionItem, { kind: "thought" }> }): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  return (
    <details className="agent-thought-card" open={expanded} onToggle={(e) => setExpanded(e.currentTarget.open)}>
      <summary className="thought-summary">
        <Icon name="sparkles" size={14} />
        <span>贾维斯思考过程</span>
        <small className="thought-toggle-hint">{expanded ? "收起思考" : "展开思考详情"}</small>
      </summary>
      <div className="thought-content">
        <p>{item.text}</p>
      </div>
    </details>
  );
}

/**
 * Component to render a Web Search / Citation card.
 */
export function SearchCard({ item }: { item: Extract<ExecutionItem, { kind: "search" }> }): JSX.Element {
  return (
    <article className="agent-tool-card tool-search">
      <div className="tool-card-header">
        <div className="tool-card-title">
          <Icon name="search" size={14} />
          <span>网络检索：</span>
          <strong>{item.query}</strong>
        </div>
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="search-link-btn"
            onClick={(e) => e.stopPropagation()}
          >
            打开来源
          </a>
        ) : null}
      </div>
      {item.summary ? <p className="tool-search-summary">{item.summary}</p> : null}
    </article>
  );
}

/**
 * Main renderer for the rich execution stream.
 */
export function RichExecutionStream({ events, task }: { events: LocalEvent[]; task: Task }): JSX.Element {
  const items = extractExecutionItems(events, task);
  const groups = groupExecutionItems(items);

  if (!items.length) {
    return (
      <div className="empty-stream-hint">
        <small>等待任务执行输出…</small>
      </div>
    );
  }

  return (
    <div className="rich-execution-stream">
      {groups.map((group) => {
        if (group.kind === "phase_track") {
          return (
            <div className="agent-phase-track" key={group.id} aria-label="任务执行阶段">
              {group.items.map((item) => (
                <div className={`agent-step-pill ${item.status}`} key={item.id}>
                  <Icon name={item.status === "completed" ? "check" : "activity"} size={13} />
                  <span>{item.title}</span>
                </div>
              ))}
            </div>
          );
        }

        const item = group.item;
        switch (item.kind) {
          case "thought":
            return <ThoughtCard key={item.id} item={item} />;
          case "command":
            return <CommandCard key={item.id} item={item} />;
          case "diff":
            return <DiffCard key={item.id} item={item} />;
          case "search":
            return <SearchCard key={item.id} item={item} />;
          case "step":
            return (
              <div className={`agent-step-pill ${item.status}`} key={item.id}>
                <Icon name={item.status === "completed" ? "check" : "activity"} size={13} />
                <span>{item.title}</span>
              </div>
            );
          case "tool":
            return (
              <div className="agent-tool-card tool-generic" key={item.id}>
                <div className="tool-card-header">
                  <div className="tool-card-title">
                    <Icon name="shield" size={14} />
                    <span>{item.summary}</span>
                  </div>
                </div>
              </div>
            );
          case "assistant_message":
            return (
              <div className="conversation-message-row assistant-message-row" key={item.id}>
                <article className="message assistant-message">
                  <span>贾维斯</span>
                  <AssistantMarkdown content={normalizeConversationMessage(item.text)} />
                </article>
              </div>
            );
        }
      })}
    </div>
  );
}
