import type { JSX } from "react";
import { Icon } from "./icons.tsx";
import type { WorkspaceFile } from "./api.ts";

export type WorkspaceFilesPanelProps = {
  workspaceName: string;
  files: WorkspaceFile[];
  loading: boolean;
  onClose: () => void;
  onRefresh: () => void;
  onOpenFile: (file: WorkspaceFile) => void;
};

function formatSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function sourceLabel(kind: WorkspaceFile["source_kind"]): string {
  return kind === "uploaded" ? "上传的资料" : "生成的文件";
}

export function WorkspaceFilesPanel({
  workspaceName,
  files,
  loading,
  onClose,
  onRefresh,
  onOpenFile
}: WorkspaceFilesPanelProps): JSX.Element {
  return (
    <section className="workspace-files-modal" role="dialog" aria-modal="true" aria-labelledby="workspace-files-title">
      <header className="workspace-files-header">
        <div>
          <span className="eyebrow">当前工作区</span>
          <h2 id="workspace-files-title">{workspaceName} 的文件</h2>
          <p>这里会显示这个工作区的对话中上传的资料和生成的文件。</p>
        </div>
        <div className="workspace-files-actions">
          <button type="button" className="icon-button" onClick={onRefresh} disabled={loading} aria-label="刷新文件">
            <Icon name="refresh" size={15} />
          </button>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭文件">
            <Icon name="x" size={15} />
          </button>
        </div>
      </header>

      {loading ? <p className="workspace-files-message">正在整理文件…</p> : null}
      {!loading && files.length === 0 ? (
        <div className="workspace-files-empty">
          <Icon name="folder" size={24} />
          <strong>这里还没有文件</strong>
          <p>在这个工作区新建对话后，上传的资料和生成的文件会自动显示在这里。</p>
        </div>
      ) : null}
      {!loading && files.length > 0 ? (
        <ul className="workspace-file-list">
          {files.map((file) => (
            <li key={`${file.source_kind}-${file.file_id}`} className="workspace-file-card">
              <div className="workspace-file-icon" aria-hidden="true">
                <Icon name={file.source_kind === "uploaded" ? "file" : "sparkles"} size={18} />
              </div>
              <div className="workspace-file-copy">
                <div className="workspace-file-title-row">
                  <strong>{file.display_name}</strong>
                  <span className={`workspace-file-kind ${file.source_kind}`}>{sourceLabel(file.source_kind)}</span>
                </div>
                <p>来自对话：{file.conversation_title}</p>
                <small>{formatSize(file.size_bytes)}{file.media_type ? ` · ${file.media_type}` : ""}</small>
              </div>
              <button type="button" className="workspace-file-open" onClick={() => onOpenFile(file)}>
                打开
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
