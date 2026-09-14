import { useState, useEffect, type JSX } from "react";
import { Icon } from "./icons.tsx";
import type { LocalApiClient } from "./api.ts";

export type TaskDiagnosticsPanelProps = {
  api: LocalApiClient;
  taskId: string;
};

export function TaskDiagnosticsPanel({ api, taskId }: TaskDiagnosticsPanelProps): JSX.Element {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeSubTab, setActiveSubTab] = useState<"overview" | "model_logs" | "tool_logs" | "retrieval">("overview");

  const fetchDiagnostics = async () => {
    if (!taskId) return;
    setLoading(true);
    setError("");
    try {
      const result = await api.taskDiagnostics(taskId);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法加载任务诊断数据");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchDiagnostics();
  }, [api, taskId]);

  if (loading && !data) {
    return (
      <div className="panel-loading-state">
        <Icon name="activity" size={18} className="spin" />
        <span>正在读取任务诊断指标…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel-error-alert" role="alert">
        <Icon name="shield" size={14} />
        <span>{error}</span>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="panel-empty-state">
        <Icon name="terminal" size={24} />
        <strong>暂无诊断数据</strong>
        <p>当前任务未生成诊断跟踪信息。</p>
      </div>
    );
  }

  const task = (data.task || {}) as Record<string, unknown>;
  const modelLogs = (Array.isArray(data.model_logs) ? data.model_logs : []) as Record<string, unknown>[];
  const toolLogs = (Array.isArray(data.tool_logs) ? data.tool_logs : []) as Record<string, unknown>[];
  const approvals = (Array.isArray(data.approvals) ? data.approvals : []) as Record<string, unknown>[];
  const retrieval = (data.retrieval || null) as Record<string, unknown> | null;

  const totalModelCalls = modelLogs.length;
  const totalToolCalls = toolLogs.length;

  return (
    <section className="diagnostics-panel" aria-label="任务执行明细">
      <header className="diagnostics-header">
        <div className="diagnostics-title-group">
          <Icon name="activity" size={16} />
          <strong>任务执行明细</strong>
          <span className="trace-id-badge">ID: {taskId.slice(0, 8)}</span>
        </div>
        <button
          type="button"
          className="icon-button compact-refresh"
          onClick={() => void fetchDiagnostics()}
          disabled={loading}
          title="刷新明细"
          aria-label="刷新执行明细"
        >
          <Icon name="refresh" size={14} className={loading ? "spin" : undefined} />
        </button>
      </header>

      <div className="diagnostics-sub-tabs">
        <button
          type="button"
          className={activeSubTab === "overview" ? "sub-tab active" : "sub-tab"}
          onClick={() => setActiveSubTab("overview")}
        >
          概览指标
        </button>
        <button
          type="button"
          className={activeSubTab === "model_logs" ? "sub-tab active" : "sub-tab"}
          onClick={() => setActiveSubTab("model_logs")}
        >
          思考交互 ({totalModelCalls})
        </button>
        <button
          type="button"
          className={activeSubTab === "tool_logs" ? "sub-tab active" : "sub-tab"}
          onClick={() => setActiveSubTab("tool_logs")}
        >
          操作执行 ({totalToolCalls})
        </button>
        {retrieval ? (
          <button
            type="button"
            className={activeSubTab === "retrieval" ? "sub-tab active" : "sub-tab"}
            onClick={() => setActiveSubTab("retrieval")}
          >
            知识检索
          </button>
        ) : null}
      </div>

      <div className="diagnostics-body">
        {activeSubTab === "overview" ? (
          <div className="diagnostics-overview">
            <div className="diag-metrics-grid">
              <div className="diag-metric-card">
                <span className="metric-label">智能思考轮次</span>
                <strong className="metric-value">{totalModelCalls}</strong>
              </div>
              <div className="diag-metric-card">
                <span className="metric-label">协助操作执行</span>
                <strong className="metric-value">{totalToolCalls}</strong>
              </div>
              <div className="diag-metric-card">
                <span className="metric-label">审批记录数</span>
                <strong className="metric-value">{approvals.length}</strong>
              </div>
              <div className="diag-metric-card">
                <span className="metric-label">任务状态</span>
                <strong className="metric-value status-text">{String(task.status || "未知")}</strong>
              </div>
            </div>

            <div className="diag-section">
              <h4>任务元数据</h4>
              <dl className="diag-meta-list">
                <div>
                  <dt>任务 ID</dt>
                  <dd><code>{taskId}</code></dd>
                </div>
                <div>
                  <dt>能力类型</dt>
                  <dd>{String(task.task_type || "default")}</dd>
                </div>
                <div>
                  <dt>创建时间</dt>
                  <dd>{String(task.created_at || "—")}</dd>
                </div>
                <div>
                  <dt>结束时间</dt>
                  <dd>{String(task.finished_at || "运行中 / 尚未完成")}</dd>
                </div>
              </dl>
            </div>
          </div>
        ) : null}

        {activeSubTab === "model_logs" ? (
          <div className="diagnostics-list">
            {modelLogs.length === 0 ? (
              <div className="panel-empty-state">
                <Icon name="terminal" size={20} />
                <span>此任务暂无模型调用日志</span>
              </div>
            ) : (
              modelLogs.map((log, idx) => (
                <article key={String(log.id || idx)} className="diag-item-card">
                  <header className="diag-item-header">
                    <strong>#{idx + 1} {String(log.model_name || "模型调用")}</strong>
                    <span className="diag-time-pill">{String(log.latency_ms || 0)} ms</span>
                  </header>
                  <div className="diag-item-details">
                    <span>输入 Tokens: {String(log.prompt_tokens || 0)}</span>
                    <span>·</span>
                    <span>输出 Tokens: {String(log.completion_tokens || 0)}</span>
                    <span>·</span>
                    <span className="status-pill">{String(log.status || "succeeded")}</span>
                  </div>
                </article>
              ))
            )}
          </div>
        ) : null}

        {activeSubTab === "tool_logs" ? (
          <div className="diagnostics-list">
            {toolLogs.length === 0 ? (
              <div className="panel-empty-state">
                <Icon name="terminal" size={20} />
                <span>此任务暂无工具调用日志</span>
              </div>
            ) : (
              toolLogs.map((log, idx) => (
                <article key={String(log.id || idx)} className="diag-item-card">
                  <header className="diag-item-header">
                    <strong>#{idx + 1} {String(log.tool_name || "工具执行")}</strong>
                    <span className="diag-time-pill">{String(log.latency_ms || 0)} ms</span>
                  </header>
                  <p className="diag-summary-text">{String(log.input_summary || log.summary || "")}</p>
                </article>
              ))
            )}
          </div>
        ) : null}

        {activeSubTab === "retrieval" && retrieval ? (
          <div className="diagnostics-retrieval">
            <div className="diag-section">
              <h4>检索 Query</h4>
              <p className="diag-query-box">{String(retrieval.query || "")}</p>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
