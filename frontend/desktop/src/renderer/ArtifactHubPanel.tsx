import { useState, type JSX } from "react";
import { Icon } from "./icons.tsx";
import {
  ARTIFACT_CATEGORIES,
  classifyArtifact,
  filterArtifacts,
  formatArtifactSize,
  type ArtifactCategory
} from "./artifact-hub.ts";
import type { LocalArtifact, LocalApiClient } from "./api.ts";

export type ArtifactHubPanelProps = {
  artifacts: LocalArtifact[];
  api: LocalApiClient;
  apiBaseUrl: string;
  userId: string;
  loading: boolean;
  onRefresh: () => void;
};

export function ArtifactHubPanel({
  artifacts,
  api,
  apiBaseUrl,
  userId,
  loading,
  onRefresh
}: ArtifactHubPanelProps): JSX.Element {
  const [selectedCategory, setSelectedCategory] = useState<ArtifactCategory>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<"all" | "active" | "archived">("all");
  const [previewArtifact, setPreviewArtifact] = useState<LocalArtifact | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const filtered = filterArtifacts(artifacts, {
    query: searchQuery,
    category: selectedCategory,
    state: stateFilter
  });

  const handleCopyId = (e: React.MouseEvent, artifactId: string) => {
    e.stopPropagation();
    void navigator.clipboard.writeText(artifactId);
    setCopiedId(artifactId);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleDownload = (artifact: LocalArtifact) => {
    void window.assistantDesktop
      .downloadArtifact({
        baseUrl: apiBaseUrl,
        artifactId: artifact.artifact_id,
        filename: artifact.filename,
        contentHash: artifact.content_hash || ""
      })
      .catch(() => {
        // Fallback for bridge implementations without cache support.
        const downloadUrl = `${apiBaseUrl}/local/artifacts/${encodeURIComponent(artifact.artifact_id)}/download?user_id=${encodeURIComponent(userId)}`;
        void window.assistantDesktop.openExternal(downloadUrl);
      });
  };

  const handleArchive = async (artifact: LocalArtifact) => {
    if (actionPending) return;
    setActionPending(true);
    try {
      await api.archiveArtifact(artifact.artifact_id);
      onRefresh();
    } finally {
      setActionPending(false);
    }
  };

  const handleRevoke = async (artifact: LocalArtifact) => {
    if (actionPending) return;
    setActionPending(true);
    try {
      await api.revokeArtifact(artifact.artifact_id);
      onRefresh();
    } finally {
      setActionPending(false);
    }
  };

  return (
    <section className="artifact-hub-panel" aria-label="生成的文件">
      <header className="artifact-hub-header">
        <div className="artifact-header-title">
          <Icon name="layers" size={16} />
          <strong>生成的文件</strong>
          <span className="artifact-count-badge">{artifacts.length}</span>
        </div>
        <button
          type="button"
          className="icon-button compact-refresh"
          onClick={onRefresh}
          disabled={loading || actionPending}
          title="刷新文件列表"
          aria-label="刷新文件列表"
        >
          <Icon name="refresh" size={14} />
        </button>
      </header>

      <div className="artifact-controls">
        <div className="artifact-search-box">
          <Icon name="search" size={14} />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索文件名称或类型…"
          />
        </div>

        <div className="artifact-category-select-wrapper">
          <select
            value={selectedCategory}
            onChange={(e) => setSelectedCategory(e.target.value as any)}
            className="artifact-category-dropdown"
            aria-label="筛选文件类型"
          >
            {ARTIFACT_CATEGORIES.map((cat) => (
              <option key={cat.value} value={cat.value}>
                {cat.label}
              </option>
            ))}
          </select>
        </div>

        <div className="artifact-category-tabs">
          {ARTIFACT_CATEGORIES.map((cat) => (
            <button
              key={cat.value}
              type="button"
              className={selectedCategory === cat.value ? "category-tab active" : "category-tab"}
              onClick={() => setSelectedCategory(cat.value)}
            >
              {cat.label}
            </button>
          ))}
        </div>
      </div>

      <div className="artifact-list-container">
        {loading ? (
          <div className="artifact-loading">
            <Icon name="activity" size={18} />
            <span>正在加载任务生成物…</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="artifact-empty-state">
            <Icon name="layers" size={24} />
            <strong>暂无生成的文件</strong>
            <p>任务生成的文档、表格、图片等文件会显示在这里。</p>
          </div>
        ) : (
          <div className="artifact-cards-list">
            {filtered.map((item) => {
              const category = classifyArtifact(item);
              const isImage = category === "image";
              const isArchived = item.lifecycle_state === "archived";
              const isRevoked = item.lifecycle_state === "revoked";

              return (
                <article
                  key={item.artifact_id}
                  className={`artifact-card ${category} state-${item.lifecycle_state}`}
                  onClick={() => setPreviewArtifact(item)}
                >
                  <div className="artifact-card-left">
                    <div className="artifact-type-icon">
                      <Icon
                        name={
                          category === "document"
                            ? "file"
                            : category === "image"
                            ? "sparkles"
                            : category === "spreadsheet"
                            ? "activity"
                            : "folder"
                        }
                        size={18}
                      />
                    </div>
                  </div>

                  <div className="artifact-card-main">
                    <div className="artifact-title-row">
                      <strong className="artifact-filename" title={item.filename}>
                        {item.filename}
                      </strong>
                      <span className="artifact-version-pill">v{item.version}</span>
                      {isArchived ? <span className="artifact-state-badge archived">已归档</span> : null}
                      {isRevoked ? <span className="artifact-state-badge revoked">已撤销</span> : null}
                    </div>

                    <div className="artifact-meta-row">
                      <span>{formatArtifactSize(item.size_bytes)}</span>
                      <span>·</span>
                      <span>{item.media_type || "binary"}</span>
                      {item.generation_method ? (
                        <>
                          <span>·</span>
                          <span className="artifact-method-tag">{item.generation_method}</span>
                        </>
                      ) : null}
                    </div>

                    <div className="artifact-id-row">
                      <code>ID: {item.artifact_id}</code>
                      <button
                        type="button"
                        className="copy-id-btn"
                        onClick={(e) => handleCopyId(e, item.artifact_id)}
                        title="复制成果 ID 用于 @ 引用"
                      >
                        <Icon name={copiedId === item.artifact_id ? "check" : "more"} size={12} />
                        {copiedId === item.artifact_id ? "已复制" : "复制 ID"}
                      </button>
                    </div>
                  </div>

                  <div className="artifact-card-actions">
                    <button
                      type="button"
                      className="artifact-action-btn primary"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownload(item);
                      }}
                      title="下载到本机"
                    >
                      <Icon name="upload" size={13} />
                      <span>下载</span>
                    </button>

                    {!isArchived && !isRevoked ? (
                      <button
                        type="button"
                        className="artifact-action-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleArchive(item);
                        }}
                        title="归档此成果"
                        disabled={actionPending}
                      >
                        归档
                      </button>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      {/* Preview Modal */}
      {previewArtifact ? (
        <div className="artifact-preview-modal" onClick={() => setPreviewArtifact(null)}>
          <div className="preview-modal-content" onClick={(e) => e.stopPropagation()}>
            <header className="preview-modal-header">
              <div className="preview-title-info">
                <strong>{previewArtifact.filename}</strong>
                <small>
                  {formatArtifactSize(previewArtifact.size_bytes)} · {previewArtifact.media_type}
                </small>
              </div>
              <div className="preview-header-actions">
                <button
                  type="button"
                  className="primary-action"
                  onClick={() => handleDownload(previewArtifact)}
                >
                  <Icon name="upload" size={14} />下载文件
                </button>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => setPreviewArtifact(null)}
                >
                  <Icon name="x" size={16} />
                </button>
              </div>
            </header>
            <div className="preview-modal-body">
              {classifyArtifact(previewArtifact) === "image" ? (
                <div className="image-preview-container">
                  <img
                    src={`${apiBaseUrl}/local/artifacts/${encodeURIComponent(previewArtifact.artifact_id)}/download?user_id=${encodeURIComponent(userId)}`}
                    alt={previewArtifact.filename}
                  />
                </div>
              ) : (
                <div className="generic-preview-info">
                  <Icon name="file" size={32} />
                  <strong>{previewArtifact.filename}</strong>
                  <p>文件大小：{formatArtifactSize(previewArtifact.size_bytes)}</p>
                  <p>Hash：<code>{previewArtifact.content_hash || "N/A"}</code></p>
                  <p>点击上方“下载文件”即可在系统默认应用中打开此成果。</p>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
