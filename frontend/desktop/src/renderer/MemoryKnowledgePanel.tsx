import { useEffect, useState, type JSX } from "react";
import { Icon } from "./icons.tsx";
import {
  MEMORY_CATEGORIES,
  filterMemories,
  filterKnowledgeDocs
} from "./memory-knowledge.ts";
import type { LocalApiClient, MemoryItem, MemoryOverview, KnowledgeDocument } from "./api.ts";

export type MemoryKnowledgePanelProps = {
  api: LocalApiClient;
  hasUser: boolean;
};

export function MemoryKnowledgePanel({ api, hasUser }: MemoryKnowledgePanelProps): JSX.Element {
  const [activeTab, setActiveTab] = useState<"memory" | "knowledge">("memory");
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [knowledgeDocs, setKnowledgeDocs] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [memoryCategory, setMemoryCategory] = useState("all");
  const [newFact, setNewFact] = useState("");
  const [newCategory, setNewCategory] = useState("general");
  const [isAddingFact, setIsAddingFact] = useState(false);
  const [error, setError] = useState("");

  const refreshData = async () => {
    if (!hasUser) return;
    setLoading(true);
    setError("");
    try {
      if (activeTab === "memory") {
        const [items, ov] = await Promise.all([
          api.listMemories().catch(() => []),
          api.memoriesOverview().catch(() => null)
        ]);
        setMemories(items);
        setOverview(ov);
      } else {
        const docs = await api.listKnowledgeDocuments().catch(() => []);
        setKnowledgeDocs(docs);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取数据失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refreshData();
  }, [activeTab, hasUser]);

  const handleAddMemory = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newFact.trim()) return;
    try {
      await api.createMemory(newFact.trim(), newCategory);
      setNewFact("");
      setIsAddingFact(false);
      void refreshData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "添加记忆失败");
    }
  };

  const handleDeleteMemory = async (memoryId: string) => {
    try {
      await api.deleteMemory(memoryId);
      setMemories((prev) => prev.filter((m) => m.id !== memoryId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除记忆失败");
    }
  };

  const filteredMemories = filterMemories(memories, searchQuery, memoryCategory);
  const filteredDocs = filterKnowledgeDocs(knowledgeDocs, searchQuery);

  return (
    <section className="memory-knowledge-panel" aria-label="记忆与知识库工作台">
      <header className="memory-hub-header">
        <div className="memory-tab-switch">
          <button
            type="button"
            className={activeTab === "memory" ? "active" : ""}
            onClick={() => {
              setActiveTab("memory");
              setSearchQuery("");
            }}
          >
            <Icon name="shield" size={14} />
            <span>工作记忆</span>
          </button>
          <button
            type="button"
            className={activeTab === "knowledge" ? "active" : ""}
            onClick={() => {
              setActiveTab("knowledge");
              setSearchQuery("");
            }}
          >
            <Icon name="folder" size={14} />
            <span>企业知识库</span>
          </button>
        </div>

        <button
          type="button"
          className="icon-button compact-refresh"
          onClick={() => void refreshData()}
          disabled={loading}
          title="刷新"
        >
          <Icon name="refresh" size={14} />
        </button>
      </header>

      {error ? <div className="memory-error-banner">{error}</div> : null}

      {activeTab === "memory" ? (
        <div className="memory-tab-content">
          <div className="memory-search-toolbar">
            <div className="search-input-wrap">
              <Icon name="search" size={13} />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索 Agent 记忆的事实与偏好…"
              />
            </div>
            <button
              type="button"
              className="primary-action add-fact-btn"
              onClick={() => setIsAddingFact(!isAddingFact)}
            >
              <Icon name={isAddingFact ? "x" : "plus"} size={13} />
              <span>{isAddingFact ? "取消" : "添加记忆"}</span>
            </button>
          </div>

          {isAddingFact ? (
            <form className="add-memory-form" onSubmit={(e) => void handleAddMemory(e)}>
              <strong>为贾维斯补充新的工作记忆</strong>
              <textarea
                value={newFact}
                onChange={(e) => setNewFact(e.target.value)}
                placeholder="例如：本项目后端使用 FastAPI + Python 3.12，开发时优先使用 uv 执行命令。"
                rows={3}
                required
              />
              <div className="form-bottom-row">
                <select value={newCategory} onChange={(e) => setNewCategory(e.target.value)}>
                  <option value="preferences">偏好与习惯</option>
                  <option value="project">项目背景</option>
                  <option value="guidelines">规则要求</option>
                  <option value="general">通用事实</option>
                </select>
                <button type="submit" className="primary-action">
                  保存记忆
                </button>
              </div>
            </form>
          ) : null}

          <div className="memory-category-filter">
            {MEMORY_CATEGORIES.map((cat) => (
              <button
                key={cat.value}
                type="button"
                className={memoryCategory === cat.value ? "active" : ""}
                onClick={() => setMemoryCategory(cat.value)}
              >
                {cat.label}
              </button>
            ))}
          </div>

          <div className="memory-items-list">
            {loading ? (
              <div className="memory-loading">正在读取长期记忆库…</div>
            ) : filteredMemories.length === 0 ? (
              <div className="memory-empty">
                <Icon name="shield" size={24} />
                <strong>暂无记忆事实</strong>
                <p>在日常协助中，贾维斯会将你的偏好和上下文沉淀为记忆；你也可以随时点击“添加记忆”补充。</p>
              </div>
            ) : (
              filteredMemories.map((item) => (
                <article className="memory-card" key={item.id}>
                  <div className="memory-card-header">
                    <span className="memory-category-badge">{item.category}</span>
                    <button
                      type="button"
                      className="memory-delete-btn"
                      onClick={() => void handleDeleteMemory(item.id)}
                      title="删除此记忆"
                    >
                      <Icon name="x" size={13} />
                    </button>
                  </div>
                  <p className="memory-fact-text">{item.fact}</p>
                </article>
              ))
            )}
          </div>
        </div>
      ) : (
        <div className="knowledge-tab-content">
          <div className="memory-search-toolbar">
            <div className="search-input-wrap">
              <Icon name="search" size={13} />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索企业知识库文档…"
              />
            </div>
          </div>

          <div className="knowledge-docs-list">
            {loading ? (
              <div className="memory-loading">正在检索知识库…</div>
            ) : filteredDocs.length === 0 ? (
              <div className="memory-empty">
                <Icon name="folder" size={24} />
                <strong>暂无知识库文档</strong>
                <p>已授权的部门知识与业务指南会展示在这里供任务自动索引与查阅。</p>
              </div>
            ) : (
              filteredDocs.map((doc) => (
                <article className="knowledge-card" key={doc.id}>
                  <div className="knowledge-card-header">
                    <Icon name="file" size={14} />
                    <strong>{doc.title}</strong>
                    <small>{doc.source_type}</small>
                  </div>
                  <p className="knowledge-summary">{doc.summary}</p>
                  {doc.content_snippet ? (
                    <details className="knowledge-snippet">
                      <summary>查看摘要片段</summary>
                      <pre><code>{doc.content_snippet}</code></pre>
                    </details>
                  ) : null}
                </article>
              ))
            )}
          </div>
        </div>
      )}
    </section>
  );
}
